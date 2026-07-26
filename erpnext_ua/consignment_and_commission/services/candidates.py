"""Read-only candidate adapters and allocation preview orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

from .allocation import (
    AllocationError,
    AllocationSlice,
    RelationshipModel,
    SourceMethod,
    StockCandidate,
    allocate_global_fifo,
)

TrackingType = Literal["NONE", "BATCH", "SERIAL"]


class CandidateAdapterError(AllocationError):
    """Raised when a source adapter cannot produce trustworthy candidates."""


@dataclass(frozen=True, slots=True)
class CandidateQuery:
    item_code: str
    company: str
    location: str
    allowed_warehouses: frozenset[str]
    serial_no: str | None = None
    batch_no: str | None = None
    fiscal_policy: str | None = None


class CandidateAdapter(Protocol):
    def load(self, query: CandidateQuery) -> list[StockCandidate]: ...


@dataclass(frozen=True, slots=True)
class CCStockLotSnapshot:
    lot_name: str
    item_code: str
    warehouse: str
    location: str
    source_method: SourceMethod
    relationship_model: RelationshipModel
    fifo_datetime: datetime
    receipt_name: str
    receipt_row_index: int
    received_qty: Decimal
    active_balance: Decimal
    reserved_qty: Decimal
    lot_status: str
    tracking_type: TrackingType
    batch_no: str | None = None
    serial_numbers: tuple[str, ...] = ()
    available_serial_numbers: tuple[str, ...] = ()
    reserved_serial_numbers: tuple[str, ...] = ()
    fiscal_policy: str | None = None
    pending_transfer: bool = False


def _validate_snapshot(snapshot: CCStockLotSnapshot) -> None:
    quantities = (snapshot.received_qty, snapshot.active_balance, snapshot.reserved_qty)
    if any(not value.is_finite() for value in quantities):
        raise CandidateAdapterError(f"CC Stock Lot {snapshot.lot_name} has a non-finite quantity")
    if snapshot.received_qty <= 0:
        raise CandidateAdapterError(f"CC Stock Lot {snapshot.lot_name} has no received quantity")
    if snapshot.active_balance < 0:
        raise CandidateAdapterError(f"CC Stock Lot {snapshot.lot_name} has a negative active balance")
    if snapshot.reserved_qty < 0 or snapshot.reserved_qty > snapshot.active_balance:
        raise CandidateAdapterError(
            f"CC Stock Lot {snapshot.lot_name} reserved quantity exceeds its active balance"
        )
    if snapshot.receipt_row_index < 1:
        raise CandidateAdapterError(f"CC Stock Lot {snapshot.lot_name} has no stable receipt row index")

    if snapshot.tracking_type == "NONE":
        if (
            snapshot.batch_no
            or snapshot.serial_numbers
            or snapshot.available_serial_numbers
            or snapshot.reserved_serial_numbers
        ):
            raise CandidateAdapterError(
                f"Untracked CC Stock Lot {snapshot.lot_name} carries a Batch/Serial identity"
            )
    elif snapshot.tracking_type == "BATCH":
        if (
            not snapshot.batch_no
            or snapshot.serial_numbers
            or snapshot.available_serial_numbers
            or snapshot.reserved_serial_numbers
        ):
            raise CandidateAdapterError(f"Batch CC Stock Lot {snapshot.lot_name} is inconsistent")
    elif snapshot.tracking_type == "SERIAL":
        if snapshot.batch_no or not snapshot.serial_numbers:
            raise CandidateAdapterError(f"Serial CC Stock Lot {snapshot.lot_name} is inconsistent")
        if snapshot.reserved_qty != snapshot.reserved_qty.to_integral_value():
            raise CandidateAdapterError(
                f"Serial CC Stock Lot {snapshot.lot_name} has fractional reserved quantity"
            )
        if snapshot.active_balance != snapshot.active_balance.to_integral_value():
            raise CandidateAdapterError(
                f"Serial CC Stock Lot {snapshot.lot_name} has a fractional active balance"
            )
        available = snapshot.available_serial_numbers
        if len(available) != len(set(available)) or not set(available).issubset(snapshot.serial_numbers):
            raise CandidateAdapterError(
                f"Serial CC Stock Lot {snapshot.lot_name} has inconsistent active Serial Nos"
            )
        if len(available) != int(snapshot.active_balance):
            raise CandidateAdapterError(
                f"Serial CC Stock Lot {snapshot.lot_name} balance does not match active Serial Nos"
            )
        reserved = snapshot.reserved_serial_numbers
        if len(reserved) != len(set(reserved)) or not set(reserved).issubset(available):
            raise CandidateAdapterError(
                f"Serial CC Stock Lot {snapshot.lot_name} has inconsistent reserved Serial Nos"
            )
        if len(reserved) != int(snapshot.reserved_qty):
            raise CandidateAdapterError(
                f"Serial CC Stock Lot {snapshot.lot_name} reservation aggregate does not match identities"
            )
    else:
        raise CandidateAdapterError(
            f"CC Stock Lot {snapshot.lot_name} has unsupported tracking type {snapshot.tracking_type}"
        )


def candidates_from_cc_stock_lot(snapshot: CCStockLotSnapshot) -> list[StockCandidate]:
    """Convert one ledger-backed CC Stock Lot snapshot into allocator candidates."""
    _validate_snapshot(snapshot)
    if snapshot.lot_status not in {"OPEN", "BLOCKED"} or snapshot.active_balance == 0:
        return []

    status = "Available" if snapshot.active_balance >= snapshot.received_qty else "Partially Sold"
    common = {
        "lot_name": snapshot.lot_name,
        "item_code": snapshot.item_code,
        "warehouse": snapshot.warehouse,
        "location": snapshot.location,
        "source_method": snapshot.source_method,
        "relationship_model": snapshot.relationship_model,
        "fifo_datetime": snapshot.fifo_datetime,
        "receipt_name": snapshot.receipt_name,
        "receipt_row_index": snapshot.receipt_row_index,
        "fiscal_policy": snapshot.fiscal_policy,
        "status": status,
        "blocked": snapshot.lot_status == "BLOCKED",
        "pending_transfer": snapshot.pending_transfer,
    }

    if snapshot.tracking_type == "SERIAL":
        return [
            StockCandidate(
                **common,
                available_qty=Decimal("1"),
                reserved_qty=(
                    Decimal("1")
                    if serial_no in snapshot.reserved_serial_numbers
                    else Decimal("0")
                ),
                serial_no=serial_no,
            )
            for serial_no in snapshot.available_serial_numbers
        ]

    return [
        StockCandidate(
            **common,
            available_qty=snapshot.active_balance,
            reserved_qty=snapshot.reserved_qty,
            batch_no=snapshot.batch_no,
        )
    ]


def preview_from_adapters(
    adapters: Sequence[CandidateAdapter],
    *,
    query: CandidateQuery,
    qty: Decimal,
) -> list[AllocationSlice]:
    """Merge trusted source adapters and run one deterministic global FIFO preview."""
    candidates = [candidate for adapter in adapters for candidate in adapter.load(query)]
    return allocate_global_fifo(
        candidates,
        item_code=query.item_code,
        location=query.location,
        qty=qty,
        allowed_warehouses=query.allowed_warehouses,
        serial_no=query.serial_no,
        batch_no=query.batch_no,
        fiscal_policy=query.fiscal_policy,
    )
