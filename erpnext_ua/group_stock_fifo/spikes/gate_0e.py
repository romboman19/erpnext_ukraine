"""Gate 0e — does one failure roll back the whole reallocation and sale?

A GSF checkout submits three documents in a row: the source Material Issue, the
target Material Receipt and the seller's Sales Invoice. If the last one fails,
the first two must leave nothing behind — otherwise stock silently moves
between two FOP companies for a sale that never happened.

The gate seeds a layer, commits it, then runs the three submissions inside a
savepoint and raises on purpose after the last one. Everything created inside
the savepoint has to disappear, and the pre-transaction balances have to come
back byte for byte.

    bench --site postest.local execute \\
      erpnext_ua.group_stock_fifo.spikes.gate_0e.run \\
      --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0E"}'
"""

from __future__ import annotations

import json
from typing import Any

from .fixtures import FOPS, ITEM_CODE, assert_scope
from .stock_setup import (
    SPIKE_MARKER,
    active_ledger_rows,
    cancel_spike_entries,
    ensure_customer,
    income_account,
    issue_to_clearing,
    purge_orphan_ledger_rows,
    receive_layer,
)

CONFIRMATION = "RUN_GATE_0E"
SAVEPOINT = "gsf_gate_0e"

SOURCE = FOPS[0]
SELLER = FOPS[2]


class InjectedFailure(RuntimeError):
    """Raised on purpose once every document of the checkout is submitted."""


def run(confirm_site: str, confirm_write: str) -> dict[str, Any]:
    import frappe

    assert_scope(frappe, confirm_site=confirm_site, confirm_write=confirm_write, expected=CONFIRMATION)
    report = _Gate0E(frappe).run()
    frappe.db.commit()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


class _Gate0E:
    def __init__(self, frappe: Any) -> None:
        self.frappe = frappe
        self.date = frappe.utils.nowdate()

    def run(self) -> dict[str, Any]:
        cleared = cancel_spike_entries(self.frappe)
        purged = purge_orphan_ledger_rows(self.frappe, ITEM_CODE)
        ensure_customer(self.frappe)
        self.frappe.db.commit()

        seed = self._seed()
        before = self._snapshot()
        before_rows = active_ledger_rows(self.frappe, ITEM_CODE)

        attempted, failure = self._attempt_checkout_and_fail()
        after = self._snapshot()
        # Compare ledger rows by identity, not by voucher name: Frappe reverts
        # the naming series when the newest document is deleted, so names from
        # an earlier run come back and a name-based check reports false hits.
        new_rows = sorted(active_ledger_rows(self.frappe, ITEM_CODE) - before_rows)
        survivors = self._surviving_documents(attempted)

        checks = {
            "injected_failure_was_raised": failure is not None,
            "no_document_survived": survivors == [],
            "no_ledger_row_survived": new_rows == [],
            "balances_restored": before == after,
            "seed_layer_intact": after["bins"].get(SOURCE.pool_warehouse, {}).get("qty") == 2.0,
        }
        return {
            "site": self.frappe.local.site,
            "posting_date": self.date,
            "cancelled_before_run": cleared,
            "purged_orphan_rows": purged,
            "seed": seed,
            "attempted_documents": attempted,
            "injected_failure": str(failure) if failure else None,
            "before": before,
            "after": after,
            "surviving_documents": survivors,
            "surviving_ledger_rows": new_rows,
            "checks": checks,
            "result": "PASS" if all(checks.values()) else "FAIL",
        }

    def _seed(self) -> str:
        name = receive_layer(
            self.frappe,
            company=SOURCE.company,
            warehouse=SOURCE.pool_warehouse,
            item_code=ITEM_CODE,
            qty=2,
            rate=1000,
            posting_date=self.date,
            posting_time="08:00:00",
            label="E-seed",
        )
        self.frappe.db.commit()
        return name

    def _attempt_checkout_and_fail(self) -> tuple[dict[str, str], Exception | None]:
        attempted: dict[str, str] = {}
        failure: Exception | None = None
        self.frappe.db.savepoint(SAVEPOINT)
        try:
            attempted["issue"] = issue_to_clearing(
                self.frappe,
                company=SOURCE.company,
                warehouse=SOURCE.pool_warehouse,
                item_code=ITEM_CODE,
                qty=2,
                posting_date=self.date,
                posting_time="09:00:00",
                label="E-issue",
            )
            attempted["receipt"] = receive_layer(
                self.frappe,
                company=SELLER.company,
                warehouse=SELLER.stage_warehouse,
                item_code=ITEM_CODE,
                qty=2,
                rate=1000,
                posting_date=self.date,
                posting_time="09:00:00",
                label="E-receipt",
            )
            attempted["sales_invoice"] = self._sell()
            raise InjectedFailure("PRRO registration failed after the sale was submitted")
        except InjectedFailure as error:
            failure = error
            self.frappe.db.rollback(save_point=SAVEPOINT)
        return attempted, failure

    def _sell(self) -> str:
        doc = self.frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "company": SELLER.company,
                "customer": ensure_customer(self.frappe),
                "set_posting_time": 1,
                "posting_date": self.date,
                "posting_time": "09:00:00",
                "update_stock": 1,
                "remarks": f"{SPIKE_MARKER} E-sale",
                "items": [
                    {
                        "item_code": ITEM_CODE,
                        "qty": 2,
                        "rate": 1800,
                        "warehouse": SELLER.stage_warehouse,
                        "income_account": income_account(self.frappe, SELLER.company),
                    }
                ],
            }
        )
        doc.insert(ignore_permissions=True)
        doc.submit()
        return doc.name

    def _snapshot(self) -> dict[str, Any]:
        warehouses = [w for fop in FOPS for w in (fop.pool_warehouse, fop.stage_warehouse)]
        bins = {}
        for warehouse in warehouses:
            row = self.frappe.db.get_value(
                "Bin",
                {"item_code": ITEM_CODE, "warehouse": warehouse},
                ["actual_qty", "stock_value"],
                as_dict=True,
            )
            bins[warehouse] = {
                "qty": float(row.actual_qty) if row else 0.0,
                "value": float(row.stock_value) if row else 0.0,
            }
        return {
            "bins": bins,
            "sle_count": self.frappe.db.count(
                "Stock Ledger Entry", {"item_code": ITEM_CODE, "is_cancelled": 0}
            ),
            "gl_count": self.frappe.db.count("GL Entry", {"is_cancelled": 0}),
        }

    def _surviving_documents(self, attempted: dict[str, str]) -> list[str]:
        doctypes = {"issue": "Stock Entry", "receipt": "Stock Entry", "sales_invoice": "Sales Invoice"}
        return [
            f"{doctypes[key]} {name}"
            for key, name in attempted.items()
            if self.frappe.db.exists(doctypes[key], name)
        ]
