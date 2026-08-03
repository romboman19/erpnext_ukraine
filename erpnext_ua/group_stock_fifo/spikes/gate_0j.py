"""Gate 0j — the §37.1 scenario end to end, decided by the real allocator.

Three FOP companies hold one shared pool at one location. The seller needs six
units. Nothing here hardcodes which layers to take: the plan comes from
`allocate_global_fifo` through the GSF candidate adapter proved in gate 0g, and
the unit costs fed to it are read back from the stock ledger rather than from
the numbers the seeding used.

Then ERPNext executes that plan — reallocation per slice, in the allocator's own
order because gate 0f showed submission order breaks ties — and the sale has to
charge exactly what the allocator computed.

    bench --site postest.local execute \\
      erpnext_ua.group_stock_fifo.spikes.gate_0j.run \\
      --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0J"}'
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from erpnext_ua.consignment_and_commission.services.candidates import CandidateQuery

from .dimension import DIMENSION_FIELD, INCOMING_DIMENSION_FIELD, ensure_layer_dimension, new_layer
from .fixtures import FOPS, ITEM_CODE, LOCATION, assert_scope
from .shared_allocator import GroupStockPool, GSFLayerSnapshot, total_cost
from .stock_setup import (
    SPIKE_MARKER,
    cancel_spike_entries,
    cogs_total,
    ensure_customer,
    income_account,
    issue_to_clearing,
    purge_orphan_ledger_rows,
    receive_layer,
    sle_rows,
)

CONFIRMATION = "RUN_GATE_0J"

SELLER = FOPS[2]
EXPECTED_COGS = 6500.0

# §37.1: two units at 1000, three at 1100, one at 1200, oldest first.
SEEDS = (
    (FOPS[0], 2, 1000.0, "08:00:00"),
    (FOPS[1], 3, 1100.0, "09:00:00"),
    (FOPS[2], 1, 1200.0, "10:00:00"),
)


def run(confirm_site: str, confirm_write: str) -> dict[str, Any]:
    import frappe

    assert_scope(frappe, confirm_site=confirm_site, confirm_write=confirm_write, expected=CONFIRMATION)
    report = _Gate0J(frappe).run()
    frappe.db.commit()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


class _Gate0J:
    def __init__(self, frappe: Any) -> None:
        self.frappe = frappe
        self.date = frappe.utils.nowdate()

    def run(self) -> dict[str, Any]:
        self._reset()
        seeded = self._seed_pool()
        pool = GroupStockPool(snapshot for _, snapshot in seeded)
        plan = pool.plan(query=self._query(pool), qty=Decimal("6"))
        planned_cost = float(total_cost(plan))

        moves = self._execute(plan, {snapshot.layer_name: layer for layer, snapshot in seeded})
        sale = self._sell()
        charged = cogs_total(self.frappe, sale)

        checks = {
            "allocator_took_all_three_owners": len(plan) == 3,
            "allocator_total_is_the_scenario_total": planned_cost == EXPECTED_COGS,
            "erpnext_charged_what_the_allocator_planned": charged == planned_cost,
            "erpnext_charged_the_scenario_total": charged == EXPECTED_COGS,
        }
        return {
            "site": self.frappe.local.site,
            "seller": SELLER.company,
            "plan": [
                {
                    "sequence": line.sequence,
                    "layer": line.layer_name,
                    "owner": line.owner_company,
                    "qty": str(line.qty),
                    "cost": str(line.cost),
                    "reallocation": line.needs_reallocation,
                }
                for line in plan
            ],
            "planned_cost": planned_cost,
            "moves": moves,
            "sale": sale,
            "charged_cogs": charged,
            "checks": checks,
            "result": "PASS" if all(checks.values()) else "FAIL",
        }

    def _reset(self) -> None:
        cancel_spike_entries(self.frappe)
        purge_orphan_ledger_rows(self.frappe, ITEM_CODE)
        ensure_layer_dimension(self.frappe)
        ensure_customer(self.frappe)
        self.frappe.db.commit()

    def _query(self, pool: GroupStockPool) -> CandidateQuery:
        return CandidateQuery(
            item_code=ITEM_CODE,
            company=SELLER.company,
            location=LOCATION,
            allowed_warehouses=pool.warehouses,
        )

    def _seed_pool(self) -> list[tuple[str, GSFLayerSnapshot]]:
        """Receive the three layers and describe them from the ledger, not the input."""
        seeded = []
        for fop, qty, rate, posting_time in SEEDS:
            layer = new_layer(self.frappe, item_code=ITEM_CODE, company=fop.company)
            voucher = receive_layer(
                self.frappe,
                company=fop.company,
                warehouse=fop.pool_warehouse,
                item_code=ITEM_CODE,
                qty=qty,
                rate=rate,
                posting_date=self.date,
                posting_time=posting_time,
                label=f"J-seed-{fop.abbr}",
                extra={INCOMING_DIMENSION_FIELD: layer},
            )
            row = sle_rows(self.frappe, voucher)[0]
            ledger_qty = Decimal(str(row["actual_qty"]))
            seeded.append(
                (
                    layer,
                    GSFLayerSnapshot(
                        layer_name=layer,
                        item_code=ITEM_CODE,
                        owner_company=fop.company,
                        warehouse=fop.pool_warehouse,
                        physical_location=LOCATION,
                        fifo_datetime=datetime.fromisoformat(f"{self.date} {posting_time}"),
                        receipt_name=voucher,
                        receipt_row_index=1,
                        active_balance=ledger_qty,
                        unit_cost=Decimal(str(row["stock_value_difference"])) / ledger_qty,
                    ),
                )
            )
        return seeded

    def _execute(self, plan: list[Any], layer_of: dict[str, str]) -> list[dict[str, Any]]:
        """Move each planned slice to the seller, in the allocator's own order."""
        moves = []
        for line in plan:
            owner = next(fop for fop, *_ in SEEDS if fop.company == line.owner_company)
            source_layer = layer_of[line.layer_name]
            target_layer = new_layer(
                self.frappe, item_code=ITEM_CODE, company=SELLER.company, source=source_layer
            )
            issue = issue_to_clearing(
                self.frappe,
                company=owner.company,
                warehouse=owner.pool_warehouse,
                item_code=ITEM_CODE,
                qty=float(line.qty),
                posting_date=self.date,
                posting_time="11:00:00",
                label=f"J-issue-{line.sequence}",
                extra={DIMENSION_FIELD: source_layer},
            )
            released = -sum(float(row["stock_value_difference"]) for row in sle_rows(self.frappe, issue))
            receipt = receive_layer(
                self.frappe,
                company=SELLER.company,
                warehouse=SELLER.stage_warehouse,
                item_code=ITEM_CODE,
                qty=float(line.qty),
                rate=released / float(line.qty),
                posting_date=self.date,
                posting_time="11:00:00",
                label=f"J-receipt-{line.sequence}",
                extra={INCOMING_DIMENSION_FIELD: target_layer},
            )
            moves.append(
                {
                    "sequence": line.sequence,
                    "owner": line.owner_company,
                    "source_layer": source_layer,
                    "target_layer": target_layer,
                    "issue": issue,
                    "receipt": receipt,
                    "released": released,
                    "planned": float(line.cost),
                }
            )
        return moves

    def _sell(self) -> str:
        doc = self.frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "company": SELLER.company,
                "customer": ensure_customer(self.frappe),
                "set_posting_time": 1,
                "posting_date": self.date,
                "posting_time": "12:00:00",
                "update_stock": 1,
                "remarks": f"{SPIKE_MARKER} J-sale",
                "items": [
                    {
                        "item_code": ITEM_CODE,
                        "qty": 6,
                        "rate": 2000,
                        "warehouse": SELLER.stage_warehouse,
                        "income_account": income_account(self.frappe, SELLER.company),
                    }
                ],
            }
        )
        doc.insert(ignore_permissions=True)
        doc.submit()
        return doc.name
