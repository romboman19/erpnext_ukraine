"""Undo a committed reallocation by reversing it, not by erasing it (§23.2).

Rollback and compensation are different operations and §23.2 is explicit about
which applies when. While every document still sits in one uncommitted
transaction, a savepoint rollback is correct and this module has nothing to do
(ADR-008). Once that transaction commits, rollback is no longer available and
cancellation is no longer honest: the stock really did move, two companies'
books really did record it, and a cancelled voucher rewrites that history.

So compensation posts **new** documents in the opposite direction. Both the
original and its reversal stay in the ledger, the clearing accounts unwind to
zero on their own, and §34.3's rule — correct by reversal, never by rewriting —
holds at the accounting level rather than only in GSF's own tables.

One consequence to be honest about: stock that comes back lands at the *back* of
its source warehouse's valuation queue, because ERPNext orders by when stock
arrived. The layer's global FIFO date is untouched, so GSF still ranks it
correctly, but the two orders now disagree for that warehouse — which is exactly
the divergence §17.2's preflight exists to catch, and it will refuse the next
allocation from that pool until the queue is settled.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import frappe
from frappe.utils import now_datetime

from ..setup.layer_dimension import INCOMING_LAYER_FIELD, LAYER_FIELD
from .clearing import clearing_pair, counterparty_values
from .domain import GSFError
from .layers import apply_to_balance, record_movement
from .reallocation import service_write, submit_stock_entry
from .staging import release_lane

COMPENSATABLE = frozenset({"PREPARED", "POSTING_SOURCE", "POSTING_DESTINATION", "COMPENSATING"})


def compensate(reallocation_name: str, *, reason: str) -> Any:
    """Post the reversing legs for a committed reallocation (§9.14).

    Idempotent by movement key: a compensation that failed halfway can be run
    again and will only post what is still missing.
    """
    reallocation = frappe.get_doc("GSF Stock Reallocation", reallocation_name)
    if reallocation.status == "COMPENSATED":
        return reallocation
    if reallocation.status not in COMPENSATABLE:
        raise GSFError(
            f"Reallocation {reallocation_name} is {reallocation.status} and cannot be compensated",
            "COMPENSATION_FAILED",
        )

    with service_write():
        reallocation.status = "COMPENSATING"
        reallocation.manual_review_reason = reason
        reallocation.save(ignore_permissions=True)

    posting = now_datetime()
    for leg in reallocation.legs:
        if leg.status == "COMPENSATED":
            continue
        moves = _leg_moves(reallocation, leg)
        if leg.is_same_company_transfer:
            _reverse_transfer(reallocation, leg, moves=moves, posting=posting)
        else:
            _reverse_intercompany(reallocation, leg, moves=moves, posting=posting)
        leg.status = "COMPENSATED"

    with service_write():
        reallocation.status = "COMPENSATED"
        reallocation.clearing_status = "BALANCED"
        reallocation.save(ignore_permissions=True)

    _close_allocation(reallocation, reason=reason)
    if reallocation.staging_lane:
        _release_lane_quietly(reallocation)
    return reallocation


def _close_allocation(reallocation: Any, *, reason: str) -> None:
    """The reservation this reallocation served is over once the stock is back.

    `release_allocation` will not decrement the positions again — preparation
    already released them as the stock left — so this only moves the status and
    records why.
    """
    if not reallocation.allocation:
        return
    from .allocations import release_allocation

    status = frappe.db.get_value("GSF Allocation", reallocation.allocation, "status")
    if status in ("RELEASED", "CONSUMED", "EXPIRED", "FAILED", "REVERSED"):
        return
    release_allocation(reallocation.allocation, reason=f"compensated: {reason}")


def _leg_moves(reallocation: Any, leg: Any) -> list[dict[str, Any]]:
    """What this leg actually moved, read back from the ledger it wrote.

    Not from the allocation: by compensation time the allocation may have been
    released, and the question here is what the *documents* did, not what was
    once planned.
    """
    rows = frappe.db.sql(
        f"""
        select `{LAYER_FIELD}` as stock_layer, sum(actual_qty) as qty,
               sum(stock_value_difference) as value
        from `tabStock Ledger Entry`
        where voucher_no = %(voucher)s and warehouse = %(warehouse)s and is_cancelled = 0
          and `{LAYER_FIELD}` is not null and `{LAYER_FIELD}` != ''
        group by `{LAYER_FIELD}`
        order by `{LAYER_FIELD}`
        """,
        {"voucher": leg.source_issue, "warehouse": leg.source_warehouse},
        as_dict=True,
    )
    if not rows:
        raise GSFError(
            f"Leg {leg.idx} of {reallocation.name} has no ledger rows to reverse",
            "COMPENSATION_FAILED",
        )
    return [
        {
            "stock_layer": row.stock_layer,
            "qty": abs(Decimal(str(row.qty))),
            "value": abs(Decimal(str(row.value))),
        }
        for row in rows
    ]


def _reverse_transfer(reallocation: Any, leg: Any, *, moves: list[dict[str, Any]], posting) -> None:
    """§14.2 in reverse: the seller's own stock goes back to its own pool."""
    entry = submit_stock_entry(
        company=leg.source_company,
        purpose="Material Transfer",
        posting=posting,
        reallocation=f"{reallocation.name} compensation",
        rows=[
            {
                "item_code": reallocation_item(reallocation),
                "qty": float(move["qty"]),
                "s_warehouse": leg.destination_stage_warehouse,
                "t_warehouse": leg.source_warehouse,
                LAYER_FIELD: move["stock_layer"],
                INCOMING_LAYER_FIELD: move["stock_layer"],
            }
            for move in moves
        ],
    )
    for move in moves:
        _record_reversal(
            reallocation,
            move=move,
            movement_type="OWN_POOL_TO_STAGE",
            voucher=entry.name,
            posting=posting,
            source_company=leg.source_company,
            source_warehouse=leg.destination_stage_warehouse,
            target_company=leg.source_company,
            target_warehouse=leg.source_warehouse,
        )
        _shift_back(leg, move)


def _reverse_intercompany(
    reallocation: Any, leg: Any, *, moves: list[dict[str, Any]], posting
) -> None:
    """§14.3 in reverse, with the clearing accounts swapped.

    The seller issues out of its stage against Due To, and the source company
    receives back against Due From. Each account is therefore debited by the
    same amount it was credited, so the pair returns to where it started
    without a manual journal.
    """
    pair = clearing_pair(
        company_group=reallocation.company_group,
        source_company=leg.source_company,
        destination_company=leg.destination_company,
    )
    item = reallocation_item(reallocation)

    issue = submit_stock_entry(
        company=leg.destination_company,
        purpose="Material Issue",
        posting=posting,
        reallocation=f"{reallocation.name} compensation",
        rows=[
            {
                "item_code": item,
                "qty": float(move["qty"]),
                "s_warehouse": leg.destination_stage_warehouse,
                "expense_account": pair.due_to_account,
                LAYER_FIELD: move["stock_layer"],
                **counterparty_values(leg.source_company),
            }
            for move in moves
        ],
    )
    receipt = submit_stock_entry(
        company=leg.source_company,
        purpose="Material Receipt",
        posting=posting,
        reallocation=f"{reallocation.name} compensation",
        rows=[
            {
                "item_code": item,
                "qty": float(move["qty"]),
                "t_warehouse": leg.source_warehouse,
                "basic_rate": float(move["value"] / move["qty"]),
                "set_basic_rate_manually": 1,
                "expense_account": pair.due_from_account,
                INCOMING_LAYER_FIELD: move["stock_layer"],
                **counterparty_values(leg.destination_company),
            }
            for move in moves
        ],
    )
    for move in moves:
        _record_reversal(
            reallocation,
            move=move,
            movement_type="INTERCOMPANY_RECEIPT",
            voucher=issue.name,
            posting=posting,
            source_company=leg.destination_company,
            source_warehouse=leg.destination_stage_warehouse,
            suffix="stage-out",
        )
        _record_reversal(
            reallocation,
            move=move,
            movement_type="INTERCOMPANY_ISSUE",
            voucher=receipt.name,
            posting=posting,
            target_company=leg.source_company,
            target_warehouse=leg.source_warehouse,
            suffix="pool-in",
        )
        _shift_back(leg, move)


def _record_reversal(
    reallocation: Any,
    *,
    move: dict[str, Any],
    movement_type: str,
    voucher: str,
    posting,
    suffix: str = "",
    **positions,
) -> None:
    """§9.11: a reversal names the movement it undoes, or it is not a reversal."""
    original = frappe.db.get_value(
        "GSF Layer Movement",
        {"idempotency_key": f"{movement_type}:{reallocation.name}:{move['stock_layer']}"},
        "name",
    )
    if not original:
        raise GSFError(
            f"No {movement_type} movement to reverse for {move['stock_layer']}",
            "COMPENSATION_FAILED",
        )
    key = f"REVERSAL:{movement_type}:{reallocation.name}:{move['stock_layer']}"
    record_movement(
        stock_layer=move["stock_layer"],
        movement_type="REVERSAL",
        posting_datetime=posting,
        qty=float(-move["qty"]),
        stock_value=float(-move["value"]),
        voucher_type="Stock Entry",
        voucher_no=voucher,
        is_reversal=1,
        reversal_of=original,
        idempotency_key=f"{key}:{suffix}" if suffix else key,
        **positions,
    )


def _shift_back(leg: Any, move: dict[str, Any]) -> None:
    """Return the §9.10 cache to the pool it came from."""
    apply_to_balance(
        stock_layer=move["stock_layer"],
        company=leg.destination_company,
        warehouse=leg.destination_stage_warehouse,
        qty=float(-move["qty"]),
        stock_value=float(-move["value"]),
    )
    apply_to_balance(
        stock_layer=move["stock_layer"],
        company=leg.source_company,
        warehouse=leg.source_warehouse,
        qty=float(move["qty"]),
        stock_value=float(move["value"]),
    )


def reallocation_item(reallocation: Any) -> str:
    """The item this reallocation moved, read off its own first leg's ledger."""
    item = frappe.db.get_value(
        "Stock Ledger Entry",
        {"voucher_no": reallocation.legs[0].source_issue, "is_cancelled": 0},
        "item_code",
    )
    if not item:
        raise GSFError(
            f"Reallocation {reallocation.name} has no ledger to read its item from",
            "COMPENSATION_FAILED",
        )
    return item


def _release_lane_quietly(reallocation: Any) -> None:
    """Free the lane, but never hide a lane that did not come back empty.

    §44 forbids cleaning a dirty lane automatically, so a refusal here is
    recorded on the reallocation and left for an operator rather than swallowed.

    The checkout name comes from the *lane's own* `current_checkout`, not from
    `reallocation.checkout`. The two are not guaranteed to be the same string:
    `prepare()` takes its own explicit `checkout` argument to acquire the lane,
    independent of whatever the allocation's `ReservationRequest` happened to
    carry. The GSF Checkout saga always threads one consistent name through
    both, so this only diverges for a caller that invokes `reserve`/`prepare`
    directly with mismatched checkout identifiers — but when it does, trusting
    `reallocation.checkout` released nothing: `release_lane` refused with
    `STAGE_LANE_BUSY` against the lane's real holder, and the stock reversal
    still succeeded while the lane stayed locked. The lane record is the source
    of truth for who holds it, so ask it instead of the reallocation's copy.
    """
    lane_checkout = (
        frappe.db.get_value("GSF Staging Lane", reallocation.staging_lane, "current_checkout")
        or reallocation.checkout
        or ""
    )
    try:
        release_lane(reallocation.staging_lane, checkout=lane_checkout)
    except GSFError as error:
        with service_write():
            reallocation.failure_code = error.code
            reallocation.manual_review_reason = (
                f"{reallocation.manual_review_reason or ''}\n{error}".strip()
            )
            reallocation.save(ignore_permissions=True)
