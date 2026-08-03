"""Gate 0k — can the preflight predict what ERPNext will consume?

§17 makes a preflight mandatory before every source issue, and §42.1 question 7
asks how to obtain the local valuation queue reliably. ADR-007 listed three
candidate approaches; this gate tests the cheapest one and, if it holds, the
other two are unnecessary.

Three runs:

* A predicts the cost of an issue that spans two layers, then performs it and
  compares the prediction against the actual ledger. The issue needs one row per
  layer — a single row spanning both is rejected by the dimension's
  negative-stock check, which is how §14.4 and §18.2 turn out to be enforced by
  the platform rather than by convention;
* B injects stock with no layer on it and checks the preflight refuses before
  anything is issued;
* C hands the preflight a planned value that does not match the queue and checks
  it reports divergence rather than passing it through as a warning.

    bench --site postest.local execute \\
      erpnext_ua.group_stock_fifo.spikes.gate_0k.run \\
      --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0K"}'
"""

from __future__ import annotations

import json
from typing import Any

from .dimension import DIMENSION_FIELD, INCOMING_DIMENSION_FIELD, ensure_layer_dimension, new_layer
from .fixtures import FOPS, ITEM_CODE, assert_scope
from .preflight import check, read_queue
from .stock_setup import (
    cancel_spike_entries,
    issue_layers_to_clearing,
    purge_orphan_ledger_rows,
    receive_layer,
    sle_rows,
)

CONFIRMATION = "RUN_GATE_0K"

FOP = FOPS[0]
# Two layers in one pool: an issue of four has to span both.
OLD = (2, 1000.0, "08:00:00")
NEW = (3, 1100.0, "09:00:00")
MOVE_QTY = 4
EXPECTED = 2 * 1000.0 + 2 * 1100.0


def run(confirm_site: str, confirm_write: str) -> dict[str, Any]:
    import frappe

    assert_scope(frappe, confirm_site=confirm_site, confirm_write=confirm_write, expected=CONFIRMATION)
    report = _Gate0K(frappe).run()
    frappe.db.commit()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


class _Gate0K:
    def __init__(self, frappe: Any) -> None:
        self.frappe = frappe
        self.date = frappe.utils.nowdate()

    def run(self) -> dict[str, Any]:
        accurate = self._run_accuracy()
        unclassified = self._run_unclassified()
        divergent = self._run_divergence()

        checks = {
            "preflight_matches_the_ledger": accurate["predicted_value"] == accurate["actual_value"],
            "preflight_needed_no_write": accurate["queue_unchanged_by_prediction"],
            "unclassified_stock_blocks": unclassified["error_code"] == "UNCLASSIFIED_GSF_STOCK",
            "wrong_plan_reports_divergence": divergent["error_code"] == "VALUATION_QUEUE_DIVERGENCE",
        }
        return {
            "site": self.frappe.local.site,
            "runs": {"A": accurate, "B": unclassified, "C": divergent},
            "checks": checks,
            "result": "PASS" if all(checks.values()) else "FAIL",
        }

    def _reset(self) -> None:
        cancel_spike_entries(self.frappe)
        purge_orphan_ledger_rows(self.frappe, ITEM_CODE)
        ensure_layer_dimension(self.frappe)
        self.frappe.db.commit()

    def _seed_two_layers(self) -> list[str]:
        layers = []
        for qty, rate, posting_time in (OLD, NEW):
            layer = new_layer(self.frappe, item_code=ITEM_CODE, company=FOP.company)
            receive_layer(
                self.frappe,
                company=FOP.company,
                warehouse=FOP.pool_warehouse,
                item_code=ITEM_CODE,
                qty=qty,
                rate=rate,
                posting_date=self.date,
                posting_time=posting_time,
                label=f"K-seed-{rate:.0f}",
                extra={INCOMING_DIMENSION_FIELD: layer},
            )
            layers.append(layer)
        return layers

    def _run_accuracy(self) -> dict[str, Any]:
        """A — predict, then issue, then compare against the real ledger."""
        self._reset()
        layers = self._seed_two_layers()

        before = read_queue(self.frappe, item_code=ITEM_CODE, warehouse=FOP.pool_warehouse)
        preflight = check(
            self.frappe,
            item_code=ITEM_CODE,
            warehouse=FOP.pool_warehouse,
            qty=MOVE_QTY,
            planned_value=EXPECTED,
            dimension_field=DIMENSION_FIELD,
        )
        after_prediction = read_queue(self.frappe, item_code=ITEM_CODE, warehouse=FOP.pool_warehouse)

        issue = issue_layers_to_clearing(
            self.frappe,
            company=FOP.company,
            warehouse=FOP.pool_warehouse,
            item_code=ITEM_CODE,
            layer_quantities=[(layers[0], OLD[0]), (layers[1], MOVE_QTY - OLD[0])],
            posting_date=self.date,
            posting_time="10:00:00",
            label="K-issue",
            dimension_field=DIMENSION_FIELD,
        )
        actual = -sum(float(row["stock_value_difference"]) for row in sle_rows(self.frappe, issue))

        return {
            "name": "A — prediction against the real ledger",
            "queue_before": before,
            "preflight": preflight,
            "predicted_value": preflight["predicted_value"],
            "issue": issue,
            "actual_value": actual,
            "expected_value": EXPECTED,
            "queue_unchanged_by_prediction": before == after_prediction,
        }

    def _run_unclassified(self) -> dict[str, Any]:
        """B — stock without a layer must stop the checkout before any issue."""
        self._reset()
        self._seed_two_layers()
        # Deliberately omit the dimension: this is what an unmanaged Stock Entry
        # into a GSF pool would look like.
        receive_layer(
            self.frappe,
            company=FOP.company,
            warehouse=FOP.pool_warehouse,
            item_code=ITEM_CODE,
            qty=1,
            rate=1500,
            posting_date=self.date,
            posting_time="09:30:00",
            label="K-unclassified",
        )
        preflight = check(
            self.frappe,
            item_code=ITEM_CODE,
            warehouse=FOP.pool_warehouse,
            qty=MOVE_QTY,
            planned_value=EXPECTED,
            dimension_field=DIMENSION_FIELD,
        )
        return {"name": "B — unclassified stock in the pool", **preflight}

    def _run_divergence(self) -> dict[str, Any]:
        """C — a plan the queue cannot satisfy must be reported, not absorbed."""
        self._reset()
        self._seed_two_layers()
        preflight = check(
            self.frappe,
            item_code=ITEM_CODE,
            warehouse=FOP.pool_warehouse,
            qty=MOVE_QTY,
            planned_value=EXPECTED - 200,
            dimension_field=DIMENSION_FIELD,
        )
        return {"name": "C — planned value disagrees with the queue", **preflight}
