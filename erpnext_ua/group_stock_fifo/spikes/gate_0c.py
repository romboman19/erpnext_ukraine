"""Gate 0c — is the COGS of the sale the cost of what GSF prepared?

Gate 0b left an open question. The FIFO queue of the Sale Stage warehouse is
ordered by arrival there, which need not match the layer global FIFO picked. So
the gate runs the sale twice:

* run A sells from a Sale Stage that holds only the current checkout. This is
  the claim of §7.3 and the number has to be the prepared cost exactly.
* run B sells from a Sale Stage that also holds an older, dearer unit, with the
  sale still tagged with the prepared layer. Whether ERPNext charges 2000 or
  2500 decides whether the inventory dimension scopes valuation or only
  validates balances — and therefore whether "Sale Stage holds one checkout"
  is an invariant or a nicety.

    bench --site postest.local execute \\
      erpnext_ua.group_stock_fifo.spikes.gate_0c.run \\
      --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0C"}'
"""

from __future__ import annotations

import json
from typing import Any

from .dimension import DIMENSION_FIELD, INCOMING_DIMENSION_FIELD, ensure_layer_dimension, new_layer
from .fixtures import FOPS, ITEM_CODE, assert_scope
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

CONFIRMATION = "RUN_GATE_0C"

SOURCE = FOPS[0]
SELLER = FOPS[2]
CONTAMINANT = FOPS[1]

PREPARED_COST = 2000.0
CONTAMINANT_RATE = 1500.0


def run(confirm_site: str, confirm_write: str) -> dict[str, Any]:
    import frappe

    assert_scope(frappe, confirm_site=confirm_site, confirm_write=confirm_write, expected=CONFIRMATION)
    report = _Gate0C(frappe).run()
    frappe.db.commit()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


class _Gate0C:
    def __init__(self, frappe: Any) -> None:
        self.frappe = frappe
        self.date = frappe.utils.nowdate()

    def run(self) -> dict[str, Any]:
        ensure_layer_dimension(self.frappe)
        ensure_customer(self.frappe)

        clean = self._run("A — Sale Stage holds only this checkout", contaminate=False)
        dirty = self._run("B — Sale Stage also holds an older dearer unit", contaminate=True)

        dirty["observed_behaviour"] = (
            "dimension scopes valuation"
            if dirty["cogs"] == PREPARED_COST
            else "warehouse FIFO wins over the dimension"
        )
        checks = {
            "clean_stage_charges_the_prepared_cost": clean["cogs"] == PREPARED_COST,
            "clean_stage_cogs_equals_sale_ledger_value": clean["cogs"] == -clean["sale_value"],
        }
        return {
            "site": self.frappe.local.site,
            "runs": [clean, dirty],
            "checks": checks,
            "result": "PASS" if all(checks.values()) else "FAIL",
            "invariant_required": dirty["cogs"] != PREPARED_COST,
        }

    def _run(self, name: str, *, contaminate: bool) -> dict[str, Any]:
        self._reset()
        source_layer = new_layer(self.frappe, item_code=ITEM_CODE, company=SOURCE.company)
        prepared_layer = new_layer(
            self.frappe, item_code=ITEM_CODE, company=SELLER.company, source=source_layer
        )

        contaminant = self._contaminate() if contaminate else None
        self._seed_source(source_layer)
        issue, receipt = self._reallocate(source_layer, prepared_layer)
        sale = self._sell(prepared_layer)

        receipt_sle = sle_rows(self.frappe, receipt)[0]
        sale_sle = sle_rows(self.frappe, sale)[0]
        return {
            "name": name,
            "layers": {"source": source_layer, "prepared": prepared_layer, "contaminant": contaminant},
            "documents": {"issue": issue, "receipt": receipt, "sale": sale},
            "prepared_value": float(receipt_sle["stock_value_difference"]),
            "sale_value": float(sale_sle["stock_value_difference"]),
            "cogs": cogs_total(self.frappe, sale),
        }

    def _reset(self) -> None:
        cancel_spike_entries(self.frappe)
        purge_orphan_ledger_rows(self.frappe, ITEM_CODE)
        self.frappe.db.commit()

    def _contaminate(self) -> str:
        """One older unit at a higher rate, owned by a third company's layer."""
        layer = new_layer(self.frappe, item_code=ITEM_CODE, company=CONTAMINANT.company)
        receive_layer(
            self.frappe,
            company=SELLER.company,
            warehouse=SELLER.stage_warehouse,
            item_code=ITEM_CODE,
            qty=1,
            rate=CONTAMINANT_RATE,
            posting_date=self.date,
            posting_time="07:00:00",
            label="C-contaminant",
            extra={INCOMING_DIMENSION_FIELD: layer},
        )
        return layer

    def _seed_source(self, source_layer: str) -> None:
        receive_layer(
            self.frappe,
            company=SOURCE.company,
            warehouse=SOURCE.pool_warehouse,
            item_code=ITEM_CODE,
            qty=2,
            rate=1000,
            posting_date=self.date,
            posting_time="08:00:00",
            label="C-seed",
            extra={INCOMING_DIMENSION_FIELD: source_layer},
        )

    def _reallocate(self, source_layer: str, prepared_layer: str) -> tuple[str, str]:
        issue = issue_to_clearing(
            self.frappe,
            company=SOURCE.company,
            warehouse=SOURCE.pool_warehouse,
            item_code=ITEM_CODE,
            qty=2,
            posting_date=self.date,
            posting_time="09:00:00",
            label="C-issue",
            extra={DIMENSION_FIELD: source_layer},
        )
        receipt = receive_layer(
            self.frappe,
            company=SELLER.company,
            warehouse=SELLER.stage_warehouse,
            item_code=ITEM_CODE,
            qty=2,
            rate=1000,
            posting_date=self.date,
            posting_time="09:00:00",
            label="C-receipt",
            extra={INCOMING_DIMENSION_FIELD: prepared_layer},
        )
        return issue, receipt

    def _sell(self, prepared_layer: str) -> str:
        doc = self.frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "company": SELLER.company,
                "customer": ensure_customer(self.frappe),
                "set_posting_time": 1,
                "posting_date": self.date,
                "posting_time": "10:00:00",
                "update_stock": 1,
                "remarks": f"{SPIKE_MARKER} C-sale",
                "items": [
                    {
                        "item_code": ITEM_CODE,
                        "qty": 2,
                        "rate": 1800,
                        "warehouse": SELLER.stage_warehouse,
                        "income_account": income_account(self.frappe, SELLER.company),
                        DIMENSION_FIELD: prepared_layer,
                    }
                ],
            }
        )
        doc.insert(ignore_permissions=True)
        doc.submit()
        return doc.name
