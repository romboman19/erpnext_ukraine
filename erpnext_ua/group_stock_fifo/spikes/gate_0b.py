"""Gate 0b — does a Material Receipt take the exact cost of the source issue?

Two runs, because there are two independent ways for the value to drift:

* run A puts stock at a *different* rate into the target warehouse first. If
  ERPNext recomputed the incoming rate from the target's own valuation instead
  of using the one GSF hands it, the receipt would land at 1500 instead of 1000
  and the gate would catch it.
* run B moves a layer whose unit cost does not divide evenly, so any rounding
  loss between issue and receipt becomes visible as a non-zero delta.

    bench --site postest.local execute \\
      erpnext_ua.group_stock_fifo.spikes.gate_0b.run \\
      --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0B"}'
"""

from __future__ import annotations

import json
from typing import Any

from .fixtures import FOPS, ITEM_CODE, assert_scope
from .stock_setup import (
    cancel_spike_entries,
    gl_rows,
    issue_to_clearing,
    pnl_total,
    receive_layer,
    sle_rows,
)

CONFIRMATION = "RUN_GATE_0B"
CLEANUP_CONFIRMATION = "CLEAN_GATE_0B"

SOURCE = FOPS[0]  # ФКРВ — oldest layer, the one global FIFO picks first
SELLER = FOPS[2]  # ФКІВ — the VAT payer, sells and therefore receives the layer
ODD = FOPS[1]  # ФКВВ — carries the layer with the non-dividing unit cost


def run(confirm_site: str, confirm_write: str) -> dict[str, Any]:
    import frappe

    assert_scope(frappe, confirm_site=confirm_site, confirm_write=confirm_write, expected=CONFIRMATION)
    gate = _Gate0B(frappe)
    try:
        report = gate.run()
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        raise
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def cleanup(confirm_site: str, confirm_write: str) -> dict[str, Any]:
    import frappe

    assert_scope(frappe, confirm_site=confirm_site, confirm_write=confirm_write, expected=CLEANUP_CONFIRMATION)
    removed = cancel_spike_entries(frappe)
    frappe.db.commit()
    report = {"site": frappe.local.site, "removed_stock_entries": removed}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


class _Gate0B:
    def __init__(self, frappe: Any) -> None:
        self.frappe = frappe
        self.date = frappe.utils.nowdate()

    def run(self) -> dict[str, Any]:
        # Each execution must start from zero stock, otherwise leftovers from a
        # previous run blend into the valuation and the numbers mean nothing.
        cleared = cancel_spike_entries(self.frappe)
        opening = self._warehouse_balances()
        if any(balance["qty"] or balance["value"] for balance in opening):
            raise RuntimeError(f"Gate 0b needs empty fixture warehouses, found {opening}")

        runs = [self._run_a(), self._run_b()]
        return {
            "site": self.frappe.local.site,
            "posting_date": self.date,
            "cancelled_before_run": cleared,
            "runs": runs,
            "result": "PASS" if all(r["verdict"] == "PASS" for r in runs) else "FAIL",
        }

    def _warehouse_balances(self) -> list[dict[str, Any]]:
        warehouses = [w for fop in FOPS for w in (fop.pool_warehouse, fop.stage_warehouse)]
        return [
            {
                "warehouse": warehouse,
                "qty": float(
                    self.frappe.db.get_value(
                        "Bin", {"item_code": ITEM_CODE, "warehouse": warehouse}, "actual_qty"
                    )
                    or 0
                ),
                "value": float(
                    self.frappe.db.get_value(
                        "Bin", {"item_code": ITEM_CODE, "warehouse": warehouse}, "stock_value"
                    )
                    or 0
                ),
            }
            for warehouse in warehouses
        ]

    def _run_a(self) -> dict[str, Any]:
        """Exact transfer while the target warehouse already values stock higher."""
        source_seed = self._receive(SOURCE, SOURCE.pool_warehouse, 2, 1000, "08:00:00", "A-seed-source")
        target_seed = self._receive(SELLER, SELLER.stage_warehouse, 1, 1500, "08:30:00", "A-seed-target")
        moved = self._reallocate(
            source=SOURCE,
            source_warehouse=SOURCE.pool_warehouse,
            qty=2,
            posting_time="09:00:00",
            label="A-move",
        )
        moved["documents"]["source_seed"] = source_seed
        moved["documents"]["target_seed_at_1500"] = target_seed
        moved["checks"]["source_released_its_own_cost"] = moved["released_by_source"] == 2000.0
        moved["checks"]["incoming_value_ignores_the_target_valuation"] = moved["accepted_by_target"] == 2000.0
        moved["verdict"] = "PASS" if all(moved["checks"].values()) else "FAIL"
        return {"name": "A — exact transfer under a contaminated target", **moved}

    def _run_b(self) -> dict[str, Any]:
        """A unit cost that does not divide evenly: 1000 over 3 units."""
        seed = self._receive(ODD, ODD.pool_warehouse, 3, 1000 / 3, "10:00:00", "B-seed")
        moved = self._reallocate(
            source=ODD,
            source_warehouse=ODD.pool_warehouse,
            qty=2,
            posting_time="10:30:00",
            label="B-move",
        )
        moved["documents"]["source_seed"] = seed
        # What an unrounded FIFO would have released for two of three units.
        moved["ideal_value"] = round(2 * (1000 / 3), 10)
        moved["rounding_vs_ideal"] = round(moved["released_by_source"] - moved["ideal_value"], 10)
        moved["verdict"] = "PASS" if all(moved["checks"].values()) else "FAIL"
        return {"name": "B — non-dividing unit cost", **moved}

    def _reallocate(
        self,
        *,
        source: Any,
        source_warehouse: str,
        qty: float,
        posting_time: str,
        label: str,
    ) -> dict[str, Any]:
        issue = issue_to_clearing(
            self.frappe,
            company=source.company,
            warehouse=source_warehouse,
            item_code=ITEM_CODE,
            qty=qty,
            posting_date=self.date,
            posting_time=posting_time,
            label=f"{label}-issue",
        )
        issue_sle = sle_rows(self.frappe, issue)
        released = -sum(float(row["stock_value_difference"]) for row in issue_sle)

        receipt = receive_layer(
            self.frappe,
            company=SELLER.company,
            warehouse=SELLER.stage_warehouse,
            item_code=ITEM_CODE,
            qty=qty,
            rate=released / qty,
            posting_date=self.date,
            posting_time=posting_time,
            label=f"{label}-receipt",
        )
        receipt_sle = sle_rows(self.frappe, receipt)
        accepted = sum(float(row["stock_value_difference"]) for row in receipt_sle)

        return {
            "documents": {"issue": issue, "receipt": receipt},
            "released_by_source": released,
            "accepted_by_target": accepted,
            "delta": round(accepted - released, 10),
            "implied_incoming_rate": round(accepted / qty, 10),
            # Balance rate of the target warehouse after the receipt, not the
            # incoming rate: ERPNext blends the moved layer with what was there.
            "target_balance_rate_after": float(receipt_sle[0]["valuation_rate"]),
            "sle": {"issue": issue_sle, "receipt": receipt_sle},
            "gl": {"issue": gl_rows(self.frappe, issue), "receipt": gl_rows(self.frappe, receipt)},
            "checks": {
                "value_transferred_exactly": accepted == released,
                "issue_leaves_pnl_untouched": pnl_total(self.frappe, issue) == 0.0,
                "receipt_leaves_pnl_untouched": pnl_total(self.frappe, receipt) == 0.0,
            },
        }

    def _receive(
        self, fop: Any, warehouse: str, qty: float, rate: float, posting_time: str, label: str
    ) -> str:
        return receive_layer(
            self.frappe,
            company=fop.company,
            warehouse=warehouse,
            item_code=ITEM_CODE,
            qty=qty,
            rate=rate,
            posting_date=self.date,
            posting_time=posting_time,
            label=label,
        )
