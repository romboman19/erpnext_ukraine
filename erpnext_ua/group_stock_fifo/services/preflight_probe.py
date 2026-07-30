"""Reads the ledger facts the §17.2 preflight decides on.

Split from `preflight` so the decision stays testable without a site (§28.3).
Everything here is a read: no write, no savepoint, no lock. Gate 0k is what
makes that possible — ERPNext persists its FIFO queue as JSON on every Stock
Ledger Entry and ships the class that consumes it, so predicting the next
consumption needs no reconstruction and no trial posting.
"""

from __future__ import annotations

import json
from decimal import Decimal

import frappe

from ..setup.layer_dimension import LAYER_FIELD
from .preflight import PreflightReport, QueueFacts, evaluate


def check(
    *,
    item_code: str,
    warehouse: str,
    selected: dict[str, Decimal],
    company_group: str,
    physical_location: str,
) -> PreflightReport:
    """Run the §17.2 preflight for one source Company/Item/Warehouse triple."""
    requested = sum(selected.values(), Decimal("0"))
    return evaluate(
        QueueFacts(
            item_code=item_code,
            warehouse=warehouse,
            requested_qty=requested,
            selected={layer: Decimal(str(qty)) for layer, qty in selected.items()},
            expected=expected_consumption(
                item_code=item_code,
                warehouse=warehouse,
                qty=requested,
                company_group=company_group,
                physical_location=physical_location,
            ),
            unclassified_qty=unclassified_qty(item_code=item_code, warehouse=warehouse),
            pending_repost=has_pending_repost(item_code=item_code, warehouse=warehouse),
            negative_stock=has_negative_stock(item_code=item_code, warehouse=warehouse),
            predicted_value=predict_value(
                item_code=item_code, warehouse=warehouse, qty=requested
            ),
        )
    )


def expected_consumption(
    *,
    item_code: str,
    warehouse: str,
    qty: Decimal,
    company_group: str,
    physical_location: str,
) -> dict[str, Decimal]:
    """Which layers ERPNext will actually eat, by FIFO order within the warehouse.

    Read from GSF's ledger positions rather than from `stock_queue`, because the
    queue is `[qty, rate]` pairs with no layer identity in them. Both orderings
    are the same one — arrival order into this warehouse — so agreeing here is
    what makes the value prediction meaningful.
    """
    from erpnext_ua.consignment_and_commission.services.candidates import CandidateQuery

    from .candidates import GSFLayerCandidateAdapter

    adapter = GSFLayerCandidateAdapter(
        company_group=company_group, physical_location=physical_location
    )
    positions = [
        position
        for position in adapter.positions(
            CandidateQuery(
                item_code=item_code,
                company="",
                location=physical_location,
                allowed_warehouses=frozenset({warehouse}),
            )
        )
        if position.warehouse == warehouse
    ]

    remaining = Decimal(str(qty))
    expected: dict[str, Decimal] = {}
    for position in positions:
        if remaining <= 0:
            break
        # The queue consumes what is physically there, regardless of what any
        # allocation has reserved: a reservation is GSF's promise, not ERPNext's.
        taken = min(position.actual_qty, remaining)
        if taken > 0:
            expected[position.stock_layer] = expected.get(position.stock_layer, Decimal("0")) + taken
            remaining -= taken
    return expected


def read_queue(*, item_code: str, warehouse: str) -> list[list[float]]:
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


def predict_value(*, item_code: str, warehouse: str, qty: Decimal) -> Decimal:
    """Replay the queue through ERPNext's own valuation class, without writing.

    Using the platform's class rather than a reimplementation is the point: a
    prediction that disagrees with ERPNext by its own rounding would be worse
    than no prediction at all.
    """
    from erpnext.stock.valuation import FIFOValuation

    queue = read_queue(item_code=item_code, warehouse=warehouse)
    replay = FIFOValuation([list(row) for row in queue])
    consumed = replay.remove_stock(float(qty))
    return Decimal(
        str(round(sum(float(bin_qty) * float(rate) for bin_qty, rate in consumed), 6))
    )


def unclassified_qty(*, item_code: str, warehouse: str) -> Decimal:
    """Stock in the warehouse carrying no layer. §17.2 requires this to be zero."""
    value = frappe.db.sql(
        f"""
        select coalesce(sum(actual_qty), 0)
        from `tabStock Ledger Entry`
        where item_code = %s and warehouse = %s and is_cancelled = 0
          and (`{LAYER_FIELD}` is null or `{LAYER_FIELD}` = '')
        """,
        (item_code, warehouse),
    )
    return Decimal(str(value[0][0])) if value else Decimal("0")


def has_pending_repost(*, item_code: str, warehouse: str) -> bool:
    """§17.2: a queued repost means the valuation queue is not settled yet.

    Transaction-based reposts name a voucher rather than an item, so they are
    matched through the ledger rows that voucher produced.
    """
    if not frappe.db.exists("DocType", "Repost Item Valuation"):
        return False
    rows = frappe.db.sql(
        """
        select 1 from `tabRepost Item Valuation` repost
        where repost.docstatus = 1
          and repost.status in ('Queued', 'In Progress')
          and (
            (repost.item_code = %(item_code)s and repost.warehouse = %(warehouse)s)
            or exists (
              select 1 from `tabStock Ledger Entry` sle
              where sle.voucher_type = repost.voucher_type
                and sle.voucher_no = repost.voucher_no
                and sle.item_code = %(item_code)s
                and sle.warehouse = %(warehouse)s
            )
          )
        limit 1
        """,
        {"item_code": item_code, "warehouse": warehouse},
    )
    return bool(rows)


def has_negative_stock(*, item_code: str, warehouse: str) -> bool:
    """Negative stock anywhere in this warehouse's history invalidates the queue."""
    rows = frappe.db.sql(
        """
        select 1 from `tabStock Ledger Entry`
        where item_code = %s and warehouse = %s and is_cancelled = 0
          and qty_after_transaction < 0
        limit 1
        """,
        (item_code, warehouse),
    )
    return bool(rows)
