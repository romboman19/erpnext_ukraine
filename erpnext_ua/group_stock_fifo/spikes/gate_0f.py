"""Gate 0f — what breaks the tie when two layers share a posting timestamp?

Global FIFO orders layers by receipt time. Two receipts in the same second are
not exotic on a busy counter, and GSF has to know whether ERPNext then consumes
in an order it can predict — or in one it cannot.

Three runs on one warehouse, each starting from empty:

* A receives the cheap layer first, then the dear one, both at 08:00:00;
* B receives them the other way round, same timestamps;
* C repeats A, to separate "deterministic" from "happened to match once".

If A and B consume different rates, the tie is broken by submission order and
GSF can control it. If they consume the same rate, the tie is broken by
something else and the design has to stop relying on timestamps.

    bench --site postest.local execute \\
      erpnext_ua.group_stock_fifo.spikes.gate_0f.run \\
      --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0F"}'
"""

from __future__ import annotations

import json
from typing import Any

from .fixtures import FOPS, ITEM_CODE, assert_scope
from .stock_setup import (
    cancel_spike_entries,
    issue_to_clearing,
    purge_orphan_ledger_rows,
    receive_layer,
    sle_rows,
)

CONFIRMATION = "RUN_GATE_0F"

FOP = FOPS[0]
SAME_SECOND = "08:00:00"
CHEAP = 1000.0
DEAR = 2000.0


def run(confirm_site: str, confirm_write: str) -> dict[str, Any]:
    import frappe

    assert_scope(frappe, confirm_site=confirm_site, confirm_write=confirm_write, expected=CONFIRMATION)
    report = _Gate0F(frappe).run()
    frappe.db.commit()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


class _Gate0F:
    def __init__(self, frappe: Any) -> None:
        self.frappe = frappe
        self.date = frappe.utils.nowdate()

    def run(self) -> dict[str, Any]:
        cheap_first = self._run("A — cheap layer submitted first", (CHEAP, DEAR))
        dear_first = self._run("B — dear layer submitted first", (DEAR, CHEAP))
        repeat = self._run("C — repeat of A", (CHEAP, DEAR))

        follows_submission = cheap_first["consumed_cost"] != dear_first["consumed_cost"]
        checks = {
            "repeat_matches_first_run": cheap_first["consumed_cost"] == repeat["consumed_cost"],
            "submission_order_decides": follows_submission,
        }
        return {
            "site": self.frappe.local.site,
            "posting_time_shared_by_both_layers": SAME_SECOND,
            "runs": [cheap_first, dear_first, repeat],
            "checks": checks,
            "tie_breaker": "submission order" if follows_submission else "not submission order",
            "result": "PASS" if all(checks.values()) else "FAIL",
        }

    def _run(self, name: str, rates: tuple[float, float]) -> dict[str, Any]:
        cancel_spike_entries(self.frappe)
        purge_orphan_ledger_rows(self.frappe, ITEM_CODE)
        self.frappe.db.commit()

        receipts = [
            receive_layer(
                self.frappe,
                company=FOP.company,
                warehouse=FOP.pool_warehouse,
                item_code=ITEM_CODE,
                qty=1,
                rate=rate,
                posting_date=self.date,
                posting_time=SAME_SECOND,
                label=f"F-{name[0]}-{index}",
            )
            for index, rate in enumerate(rates, start=1)
        ]
        issue = issue_to_clearing(
            self.frappe,
            company=FOP.company,
            warehouse=FOP.pool_warehouse,
            item_code=ITEM_CODE,
            qty=1,
            posting_date=self.date,
            posting_time="09:00:00",
            label=f"F-{name[0]}-issue",
        )
        consumed = -float(sle_rows(self.frappe, issue)[0]["stock_value_difference"])
        return {
            "name": name,
            "submitted_rates": list(rates),
            "receipts": receipts,
            "issue": issue,
            "consumed_cost": consumed,
            "consumed": "first submitted" if consumed == rates[0] else "second submitted",
        }
