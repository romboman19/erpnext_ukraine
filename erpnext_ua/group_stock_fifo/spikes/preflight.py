"""§17 preflight: predict what ERPNext will consume, before issuing anything.

The key finding behind this module is that no reconstruction is needed. ERPNext
persists its FIFO queue as JSON on every Stock Ledger Entry, and ships the class
that consumes it. Reading the latest row and replaying it through the platform's
own `FIFOValuation` gives the exact bins the next issue will take — in memory,
with no write and no savepoint.
"""

from __future__ import annotations

import json
from typing import Any

TOLERANCE = 0.01


def read_queue(frappe: Any, *, item_code: str, warehouse: str) -> list[list[float]]:
    """The FIFO queue ERPNext will consume from, straight off the latest SLE."""
    rows = frappe.db.sql(
        """
        select stock_queue
        from `tabStock Ledger Entry`
        where item_code = %s and warehouse = %s and is_cancelled = 0
        order by posting_date desc, posting_time desc, creation desc
        limit 1
        """,
        (item_code, warehouse),
        as_dict=True,
    )
    return json.loads(rows[0]["stock_queue"] or "[]") if rows else []


def predict_consumption(queue: list[list[float]], qty: float) -> dict[str, Any]:
    """Replay the queue through ERPNext's own valuation class, without writing."""
    from erpnext.stock.valuation import FIFOValuation

    replay = FIFOValuation([list(row) for row in queue])
    consumed = replay.remove_stock(qty)
    return {
        "bins": [[float(bin_qty), float(rate)] for bin_qty, rate in consumed],
        "value": round(sum(float(bin_qty) * float(rate) for bin_qty, rate in consumed), 6),
        "queue_after": [[float(q), float(r)] for q, r in list(replay)],
    }


def unclassified_qty(frappe: Any, *, item_code: str, warehouse: str, dimension_field: str) -> float:
    """Stock in the warehouse that carries no layer. §17.2 requires this to be zero."""
    value = frappe.db.sql(
        f"""
        select coalesce(sum(actual_qty), 0)
        from `tabStock Ledger Entry`
        where item_code = %s and warehouse = %s and is_cancelled = 0
          and (`{dimension_field}` is null or `{dimension_field}` = '')
        """,
        (item_code, warehouse),
    )
    return float(value[0][0]) if value else 0.0


def check(
    frappe: Any,
    *,
    item_code: str,
    warehouse: str,
    qty: float,
    planned_value: float,
    dimension_field: str,
) -> dict[str, Any]:
    """Run the §17.2 preflight for one source Company/Item/Warehouse triple."""
    queue = read_queue(frappe, item_code=item_code, warehouse=warehouse)
    prediction = predict_consumption(queue, qty)
    unclassified = unclassified_qty(
        frappe, item_code=item_code, warehouse=warehouse, dimension_field=dimension_field
    )
    delta = round(prediction["value"] - planned_value, 6)

    error_code = None
    if unclassified:
        error_code = "UNCLASSIFIED_GSF_STOCK"
    elif abs(delta) > TOLERANCE:
        error_code = "VALUATION_QUEUE_DIVERGENCE"

    return {
        "warehouse": warehouse,
        "queue_before": queue,
        "requested_qty": qty,
        "planned_value": planned_value,
        "predicted_value": prediction["value"],
        "predicted_bins": prediction["bins"],
        "delta": delta,
        "unclassified_qty": unclassified,
        "ok": error_code is None,
        "error_code": error_code,
    }
