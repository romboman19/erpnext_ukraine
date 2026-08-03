"""Just-in-time reallocation of reserved stock into the seller's stage (§14–§16).

This is where the group's promise is kept: the cashier scanned a barcode, the
allocator decided that the oldest units belong to two other FOPs, and this
service moves them without inventing a sale between the companies.

Three rules shape every line below.

**The value is read, never computed.** §16.1 lists the tempting sources — last
purchase rate, item valuation, the price list — and forbids all of them. The
destination receipt is built from `stock_value_difference` on the source's
actual ledger rows, after that source document is submitted. ADR-003.

**One row per layer.** Gate 0k found this is a platform rule, not a preference:
the dimension's negative-stock check rejects a single row spanning two layers,
so §14.4 and §18.2 are enforced by ERPNext whether or not we agree.

**The counter-entry is a balance-sheet account.** §15 makes the whole mode
meaningless otherwise — an expense account would book a loss in the source
company and a windfall in the seller, for stock that never left the building.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import frappe
from frappe.utils import now_datetime

from ..setup.layer_dimension import INCOMING_LAYER_FIELD, LAYER_FIELD
from .clearing import ClearingPair, clearing_pair, counterparty_values
from .domain import GSFError, balance_identity
from .layers import apply_to_balance, record_movement
from .preflight import assert_ok
from .preflight_probe import check as preflight_check
from .preflight_probe import check_serials as serial_preflight_check
from .reservation import (
    ALLOCATION_PREPARED,
    ALLOCATION_PREPARING,
    ALLOCATION_RESERVED,
    validate_allocation_transition,
)
from .serial_identity import tracking_values
from .staging import acquire_lane

WRITE_FLAG = "gsf_reallocation_service"
ALLOCATION_WRITE_FLAG = "gsf_allocation_service"


@contextmanager
def service_write():
    for flag in (WRITE_FLAG, ALLOCATION_WRITE_FLAG):
        setattr(frappe.flags, flag, True)
    try:
        yield
    finally:
        for flag in (WRITE_FLAG, ALLOCATION_WRITE_FLAG):
            setattr(frappe.flags, flag, False)


@dataclass(frozen=True, slots=True)
class SliceMove:
    """One layer's quantity leaving one pool for the seller's stage."""

    stock_layer: str
    source_company: str
    source_warehouse: str
    qty: Decimal
    serial_no: str | None = None
    batch_no: str | None = None


def prepare(allocation_name: str, *, checkout: str, staging_lane: str | None = None) -> Any:
    """Move every reserved slice into the seller's stage lane (§14.1).

    Runs inside the caller's transaction and never commits (§14.6): if the sale
    that follows fails, the issue, the receipt and the transfer must all roll
    back together, and a commit here would strand stock in a lane.
    """
    allocation = frappe.get_doc("GSF Allocation", allocation_name)
    validate_allocation_transition(allocation.status, ALLOCATION_PREPARING)
    if allocation.status != ALLOCATION_RESERVED:
        raise GSFError(
            f"Allocation {allocation_name} is {allocation.status}, not reserved",
            "ALLOCATION_CONFLICT",
        )

    lane = staging_lane or acquire_lane(
        company=allocation.seller_company,
        physical_location=allocation.physical_location,
        checkout=checkout,
    )
    stage_warehouse = frappe.db.get_value("GSF Staging Lane", lane, "warehouse")
    if not stage_warehouse:
        raise GSFError(f"Staging lane {lane} has no warehouse", "WAREHOUSE_BINDING_MISSING")

    moves = [
        SliceMove(
            stock_layer=row.stock_layer,
            source_company=row.source_company,
            source_warehouse=row.source_warehouse,
            qty=Decimal(str(row.qty or 0)),
            serial_no=row.serial_no or None,
            batch_no=row.batch_no or None,
        )
        for row in allocation.slices
    ]
    _run_preflight(allocation, moves)

    posting = now_datetime()
    reallocation = _new_reallocation(
        allocation, lane=lane, moves=moves, posting_datetime=posting
    )

    with service_write():
        allocation.status = ALLOCATION_PREPARING
        allocation.save(ignore_permissions=True)

    legs = []
    # Stage receipts must follow the exact global FIFO plan. ERPNext uses
    # submission order as the valuation tie-breaker at one timestamp, so
    # sorting legal entities (or always moving the seller first) swaps costs
    # whenever the older layer belongs to a later-sorted company. Contiguous
    # runs also preserve an A -> B -> A plan without inventing time offsets.
    for company, group in _source_runs(moves):
        if company == allocation.seller_company:
            legs.append(
                _transfer_own(
                    allocation,
                    reallocation,
                    moves=group,
                    stage_warehouse=stage_warehouse,
                    posting=posting,
                )
            )
        else:
            legs.append(
                _reallocate_foreign(
                    allocation,
                    reallocation,
                    source_company=company,
                    moves=group,
                    stage_warehouse=stage_warehouse,
                    posting=posting,
                )
            )

    _settle(allocation, reallocation, legs)
    return reallocation


def _source_runs(moves: list[SliceMove]) -> list[tuple[str, list[SliceMove]]]:
    """Keep planned FIFO order while using one legal document per contiguous run."""
    grouped: list[tuple[str, list[SliceMove]]] = []
    for move in moves:
        if grouped and grouped[-1][0] == move.source_company:
            grouped[-1][1].append(move)
        else:
            grouped.append((move.source_company, [move]))
    return grouped


def _run_preflight(allocation: Any, moves: list[SliceMove]) -> None:
    """§17.2, once per source warehouse, before anything is posted."""
    by_warehouse: dict[str, list[SliceMove]] = defaultdict(list)
    for move in moves:
        by_warehouse[move.source_warehouse].append(move)

    assert_ok(
        [
            _preflight_for_warehouse(allocation, warehouse, warehouse_moves)
            for warehouse, warehouse_moves in sorted(by_warehouse.items())
        ]
    )


def _preflight_for_warehouse(allocation: Any, warehouse: str, moves: list[SliceMove]):
    selected: dict[str, Decimal] = {}
    for move in moves:
        selected[move.stock_layer] = selected.get(move.stock_layer, Decimal("0")) + move.qty

    serial_moves = [move for move in moves if move.serial_no]
    if serial_moves:
        if len(serial_moves) != len(moves):
            raise GSFError(
                f"Warehouse {warehouse} mixes Serial and non-Serial allocation slices",
                "SERIAL_AMBIGUOUS",
            )
        serial_layers = {move.serial_no: move.stock_layer for move in serial_moves}
        if len(serial_layers) != len(serial_moves):
            raise GSFError(
                f"Warehouse {warehouse} contains a duplicate Serial allocation",
                "SERIAL_AMBIGUOUS",
            )
        return serial_preflight_check(
            item_code=allocation.item_code,
            warehouse=warehouse,
            selected=selected,
            serial_layers=serial_layers,
        )

    return preflight_check(
        item_code=allocation.item_code,
        warehouse=warehouse,
        selected=selected,
        company_group=allocation.company_group,
        physical_location=allocation.physical_location,
    )


def _new_reallocation(allocation: Any, *, lane: str, moves: list[SliceMove], posting_datetime) -> Any:
    doc = frappe.get_doc(
        {
            "doctype": "GSF Stock Reallocation",
            "status": "POSTING_SOURCE",
            "reallocation_mode": "MANAGEMENT_REALLOCATION",
            "company_group": allocation.company_group,
            "physical_location": allocation.physical_location,
            "seller_company": allocation.seller_company,
            "checkout": allocation.checkout,
            "staging_lane": lane,
            "posting_datetime": posting_datetime,
            "total_qty": float(sum((move.qty for move in moves), Decimal("0"))),
            "allocation": allocation.name,
            "allocation_set_hash": _slice_set_hash(moves),
        }
    )
    with service_write():
        doc.insert(ignore_permissions=True)
    return doc


def _slice_set_hash(moves: list[SliceMove]) -> str:
    """A stable fingerprint of what this document was built to move."""
    payload = "|".join(
        f"{move.stock_layer}:{move.source_warehouse}:{move.qty}:{move.serial_no or ''}:{move.batch_no or ''}"
        for move in sorted(
            moves,
            key=lambda m: (
                m.source_warehouse,
                m.stock_layer,
                m.serial_no or "",
                m.batch_no or "",
            ),
        )
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _transfer_own(
    allocation: Any, reallocation: Any, *, moves: list[SliceMove], stage_warehouse: str, posting
) -> dict[str, Any]:
    """§14.2: the seller's own stock moves by ordinary Material Transfer.

    No clearing account and no destination receipt — nothing changes hands, so
    inventing an intercompany posting here would create a balance that never
    unwinds.
    """
    source_warehouse = moves[0].source_warehouse
    entry = submit_stock_entry(
        company=allocation.seller_company,
        purpose="Material Transfer",
        posting=posting,
        reallocation=reallocation.name,
        rows=[
            {
                "item_code": allocation.item_code,
                "qty": float(move.qty),
                "s_warehouse": move.source_warehouse,
                "t_warehouse": stage_warehouse,
                LAYER_FIELD: move.stock_layer,
                # The same layer on both legs (§14.2): a transfer moves stock,
                # it does not create a new cost identity.
                INCOMING_LAYER_FIELD: move.stock_layer,
                **_tracking_values(move),
            }
            for move in moves
        ],
    )
    value = _issued_value(entry, source_warehouse)
    for move in moves:
        record_movement(
            stock_layer=move.stock_layer,
            movement_type="OWN_POOL_TO_STAGE",
            posting_datetime=posting,
            qty=float(move.qty),
            stock_value=float(_row_value(entry, move, source_warehouse)),
            source_company=allocation.seller_company,
            source_warehouse=move.source_warehouse,
            target_company=allocation.seller_company,
            target_warehouse=stage_warehouse,
            voucher_type="Stock Entry",
            voucher_no=entry.name,
            idempotency_key=f"OWN_POOL_TO_STAGE:{reallocation.name}:{move.stock_layer}",
        )
        _shift_balance(
            move, allocation=allocation, stage_warehouse=stage_warehouse, entry=entry
        )
    return {
        "status": "PREPARED",
        "is_same_company_transfer": 1,
        "source_company": allocation.seller_company,
        "destination_company": allocation.seller_company,
        "source_warehouse": source_warehouse,
        "destination_stage_warehouse": stage_warehouse,
        "qty": float(sum((move.qty for move in moves), Decimal("0"))),
        "source_issue": entry.name,
        "source_stock_value": float(value),
        "destination_stock_value": float(value),
        "difference": 0.0,
        "slice_set_hash": _slice_set_hash(moves),
    }


def _reallocate_foreign(
    allocation: Any,
    reallocation: Any,
    *,
    source_company: str,
    moves: list[SliceMove],
    stage_warehouse: str,
    posting,
) -> dict[str, Any]:
    """§14.3: issue out of the source company, receive into the seller's stage.

    Not a Material Transfer: ERPNext's transfer moves stock within one company's
    books, and these are two separate legal entities whose accounts must each
    stay complete on their own.
    """
    pair = clearing_pair(
        company_group=allocation.company_group,
        source_company=source_company,
        destination_company=allocation.seller_company,
    )
    source_warehouse = moves[0].source_warehouse

    issue = submit_stock_entry(
        company=source_company,
        purpose="Material Issue",
        posting=posting,
        reallocation=reallocation.name,
        rows=[
            {
                "item_code": allocation.item_code,
                "qty": float(move.qty),
                "s_warehouse": move.source_warehouse,
                "expense_account": pair.due_from_account,
                LAYER_FIELD: move.stock_layer,
                **_tracking_values(move),
                **counterparty_values(allocation.seller_company),
            }
            for move in moves
        ],
    )

    # Everything below reads from the ledger the issue just wrote. Nothing here
    # recomputes a value, and the receipt cannot be built before this point.
    row_values = {move.stock_layer: _row_value(issue, move, source_warehouse) for move in moves}
    source_value = sum(row_values.values(), Decimal("0"))

    receipt = submit_stock_entry(
        company=allocation.seller_company,
        purpose="Material Receipt",
        posting=posting,
        reallocation=reallocation.name,
        rows=[
            {
                "item_code": allocation.item_code,
                "qty": float(move.qty),
                "t_warehouse": stage_warehouse,
                "basic_rate": float(row_values[move.stock_layer] / move.qty),
                "set_basic_rate_manually": 1,
                "expense_account": pair.due_to_account,
                INCOMING_LAYER_FIELD: move.stock_layer,
                **_tracking_values(move),
                **counterparty_values(source_company),
            }
            for move in moves
        ],
    )
    destination_value = _received_value(receipt, stage_warehouse)
    _assert_exact_transfer(source_value, destination_value, source_company=source_company)

    for move in moves:
        _record_foreign_movements(
            allocation,
            reallocation,
            move=move,
            pair=pair,
            issue=issue,
            receipt=receipt,
            stage_warehouse=stage_warehouse,
            posting=posting,
            value=row_values[move.stock_layer],
        )
        _shift_balance(
            move, allocation=allocation, stage_warehouse=stage_warehouse, entry=issue
        )

    return {
        "status": "PREPARED",
        "is_same_company_transfer": 0,
        "source_company": source_company,
        "destination_company": allocation.seller_company,
        "counterparty_company": source_company,
        "source_warehouse": source_warehouse,
        "destination_stage_warehouse": stage_warehouse,
        "qty": float(sum((move.qty for move in moves), Decimal("0"))),
        "source_issue": issue.name,
        "destination_receipt": receipt.name,
        "source_stock_value": float(source_value),
        "destination_stock_value": float(destination_value),
        "difference": float(source_value - destination_value),
        "source_clearing_account": pair.due_from_account,
        "destination_clearing_account": pair.due_to_account,
        "slice_set_hash": _slice_set_hash(moves),
    }


def _record_foreign_movements(
    allocation: Any,
    reallocation: Any,
    *,
    move: SliceMove,
    pair: ClearingPair,
    issue: Any,
    receipt: Any,
    stage_warehouse: str,
    posting,
    value: Decimal,
) -> None:
    record_movement(
        stock_layer=move.stock_layer,
        movement_type="INTERCOMPANY_ISSUE",
        posting_datetime=posting,
        qty=float(move.qty),
        stock_value=float(value),
        source_company=pair.source_company,
        source_warehouse=move.source_warehouse,
        voucher_type="Stock Entry",
        voucher_no=issue.name,
        idempotency_key=f"INTERCOMPANY_ISSUE:{reallocation.name}:{move.stock_layer}",
    )
    record_movement(
        stock_layer=move.stock_layer,
        movement_type="INTERCOMPANY_RECEIPT",
        posting_datetime=posting,
        qty=float(move.qty),
        stock_value=float(value),
        target_company=pair.destination_company,
        target_warehouse=stage_warehouse,
        voucher_type="Stock Entry",
        voucher_no=receipt.name,
        idempotency_key=f"INTERCOMPANY_RECEIPT:{reallocation.name}:{move.stock_layer}",
    )


def _assert_exact_transfer(source: Decimal, destination: Decimal, *, source_company: str) -> None:
    """§16.2: the two legs must carry the same value, to the configured tolerance."""
    tolerance = Decimal(
        str(frappe.db.get_single_value("GSF Settings", "currency_tolerance") or "0.01")
    )
    gap = abs(source - destination)
    if gap > tolerance:
        raise GSFError(
            f"{source_company} issued {source} but the seller received {destination}; "
            f"the gap {gap} exceeds the tolerance {tolerance}",
            "TRANSFER_VALUE_MISMATCH",
        )


def _shift_balance(move: SliceMove, *, allocation: Any, stage_warehouse: str, entry: Any) -> None:
    """Move the §9.10 cache with the stock: out of the pool, into the stage.

    The hold on the source position is released in the same write that removes
    the quantity. The reservation and the stock it was holding stop existing at
    the same instant, so releasing afterwards would briefly leave the row
    claiming a hold on stock it no longer has.
    """
    value = _row_value(entry, move, move.source_warehouse)
    apply_to_balance(
        stock_layer=move.stock_layer,
        company=move.source_company,
        warehouse=move.source_warehouse,
        qty=float(-move.qty),
        stock_value=float(-value),
        reserved_delta=float(-move.qty),
    )
    apply_to_balance(
        stock_layer=move.stock_layer,
        company=allocation.seller_company,
        warehouse=stage_warehouse,
        qty=float(move.qty),
        stock_value=float(value),
    )


def submit_stock_entry(*, company: str, purpose: str, posting, reallocation: str, rows: list) -> Any:
    entry = frappe.get_doc(
        {
            "doctype": "Stock Entry",
            "stock_entry_type": purpose,
            "purpose": purpose,
            "company": company,
            "set_posting_time": 1,
            "posting_date": posting.date(),
            "posting_time": posting.time(),
            # §17.3 refuses unmanaged documents into a GSF pool; this flag is
            # what separates a GSF flow from a hand-written Stock Entry.
            "gsf_managed": 1,
            "remarks": f"GSF reallocation {reallocation}",
            "items": rows,
        }
    )
    entry.insert(ignore_permissions=True)
    entry.submit()
    return entry


def _tracking_values(move: SliceMove) -> dict[str, str | int]:
    return tracking_values(move.serial_no, move.batch_no)


def _issued_value(entry: Any, warehouse: str) -> Decimal:
    """§16.2: `abs(sum(stock_value_difference))` off the source's own rows."""
    value = frappe.db.sql(
        """
        select coalesce(sum(stock_value_difference), 0) from `tabStock Ledger Entry`
        where voucher_type = 'Stock Entry' and voucher_no = %s and warehouse = %s
          and is_cancelled = 0
        """,
        (entry.name, warehouse),
    )[0][0]
    return abs(Decimal(str(value or 0)))


def _received_value(entry: Any, warehouse: str) -> Decimal:
    value = frappe.db.sql(
        """
        select coalesce(sum(stock_value_difference), 0) from `tabStock Ledger Entry`
        where voucher_type = 'Stock Entry' and voucher_no = %s and warehouse = %s
          and is_cancelled = 0
        """,
        (entry.name, warehouse),
    )[0][0]
    return Decimal(str(value or 0))


def _row_value(entry: Any, move: SliceMove, warehouse: str) -> Decimal:
    """The value of one layer's row, taken from the ledger row it produced."""
    value = frappe.db.sql(
        f"""
        select coalesce(sum(stock_value_difference), 0) from `tabStock Ledger Entry`
        where voucher_type = 'Stock Entry' and voucher_no = %(voucher)s
          and warehouse = %(warehouse)s and `{LAYER_FIELD}` = %(layer)s
          and is_cancelled = 0
        """,
        {"voucher": entry.name, "warehouse": warehouse, "layer": move.stock_layer},
    )[0][0]
    return abs(Decimal(str(value or 0)))


def _settle(allocation: Any, reallocation: Any, legs: list[dict[str, Any]]) -> None:
    """Record the legs, then move both documents to PREPARED together."""
    source_total = sum((Decimal(str(leg["source_stock_value"])) for leg in legs), Decimal("0"))
    destination_total = sum(
        (Decimal(str(leg["destination_stock_value"])) for leg in legs), Decimal("0")
    )

    with service_write():
        for leg in legs:
            reallocation.append("legs", leg)
        reallocation.total_source_value = float(source_total)
        reallocation.total_destination_value = float(destination_total)
        reallocation.value_difference = float(source_total - destination_total)
        reallocation.clearing_status = (
            "BALANCED" if source_total == destination_total else "IMBALANCED"
        )
        reallocation.status = "PREPARED"
        reallocation.save(ignore_permissions=True)

        # Each leg already released its own hold as the stock left, so the
        # allocation is marked settled rather than released again — a second
        # decrement would hand back units another allocation still holds.
        allocation.positions_released = 1
        allocation.status = ALLOCATION_PREPARED
        allocation.save(ignore_permissions=True)


def stage_value(*, stock_layer: str, warehouse: str) -> Decimal:
    """§16.4's `prepared_stage_value`, for the sale to check itself against."""
    name = balance_identity(
        stock_layer=stock_layer,
        company=frappe.db.get_value("Warehouse", warehouse, "company"),
        warehouse=warehouse,
    )
    return Decimal(str(frappe.db.get_value("GSF Layer Balance", name, "stock_value_cache") or 0))
