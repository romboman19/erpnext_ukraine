"""Deterministic global FIFO allocation across technical warehouses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

RelationshipModel = Literal["OWN", "COMMISSION", "CONSIGNMENT"]
SourceMethod = Literal["BUYOUT", "DEFERRED_PURCHASE", "COMMISSION", "CONSIGNMENT"]

SOURCE_METHOD_RELATIONSHIP_MODEL: dict[SourceMethod, RelationshipModel] = {
    "BUYOUT": "OWN",
    "DEFERRED_PURCHASE": "OWN",
    "COMMISSION": "COMMISSION",
    "CONSIGNMENT": "CONSIGNMENT",
}


class AllocationError(ValueError):
    """Base allocation-domain error."""


class InsufficientStockError(AllocationError):
    """Raised when eligible candidates cannot satisfy the requested quantity."""


class AmbiguousSerialError(AllocationError):
    """Raised when one serial number resolves to more than one active candidate."""


@dataclass(frozen=True, slots=True)
class StockCandidate:
    lot_name: str
    item_code: str
    warehouse: str
    location: str
    source_method: SourceMethod
    relationship_model: RelationshipModel
    fifo_datetime: datetime
    receipt_name: str
    receipt_row_index: int
    available_qty: Decimal
    reserved_qty: Decimal = Decimal("0")
    serial_no: str | None = None
    batch_no: str | None = None
    fiscal_policy: str | None = None
    status: str = "Available"
    blocked: bool = False
    pending_transfer: bool = False

    def __post_init__(self) -> None:
        expected_model = SOURCE_METHOD_RELATIONSHIP_MODEL.get(self.source_method)
        if not expected_model:
            raise AllocationError(f"Unsupported stock source method: {self.source_method}")
        if self.relationship_model != expected_model:
            raise AllocationError(
                f"Stock source method {self.source_method} requires relationship model {expected_model}"
            )

    @property
    def allocatable_qty(self) -> Decimal:
        return max(self.available_qty - self.reserved_qty, Decimal("0"))


@dataclass(frozen=True, slots=True)
class AllocationSlice:
    sequence: int
    lot_name: str
    warehouse: str
    source_method: SourceMethod
    relationship_model: RelationshipModel
    qty: Decimal
    serial_no: str | None
    batch_no: str | None
    fifo_datetime: datetime
    receipt_name: str
    receipt_row_index: int


def _fifo_key(candidate: StockCandidate) -> tuple[datetime, str, int, str]:
    return (
        candidate.fifo_datetime,
        candidate.receipt_name,
        candidate.receipt_row_index,
        candidate.lot_name,
    )


def _eligible_candidates(
    candidates: list[StockCandidate],
    *,
    item_code: str,
    location: str,
    allowed_warehouses: frozenset[str],
    serial_no: str | None,
    batch_no: str | None,
    fiscal_policy: str | None,
) -> list[StockCandidate]:
    eligible = [
        candidate
        for candidate in candidates
        if candidate.item_code == item_code
        and candidate.location == location
        and candidate.warehouse in allowed_warehouses
        and candidate.status in {"Available", "Partially Sold"}
        and not candidate.blocked
        and not candidate.pending_transfer
        and candidate.allocatable_qty > 0
        and (not fiscal_policy or candidate.fiscal_policy in {None, fiscal_policy})
        and (not serial_no or candidate.serial_no == serial_no)
        and (not batch_no or candidate.batch_no == batch_no)
    ]

    if serial_no and len(eligible) > 1:
        raise AmbiguousSerialError(f"Serial No {serial_no!r} has multiple active stock candidates")
    return sorted(eligible, key=_fifo_key)


def allocate_global_fifo(
    candidates: list[StockCandidate],
    *,
    item_code: str,
    location: str,
    qty: Decimal,
    allowed_warehouses: frozenset[str],
    serial_no: str | None = None,
    batch_no: str | None = None,
    fiscal_policy: str | None = None,
) -> list[AllocationSlice]:
    """Allocate exact quantity using Serial, Batch, then global FIFO priority."""
    if qty <= 0:
        raise AllocationError("Allocation quantity must be greater than zero")
    if not allowed_warehouses:
        raise AllocationError("At least one technical warehouse must be allowed")

    eligible = _eligible_candidates(
        candidates,
        item_code=item_code,
        location=location,
        allowed_warehouses=allowed_warehouses,
        serial_no=serial_no,
        batch_no=batch_no,
        fiscal_policy=fiscal_policy,
    )

    remaining = qty
    allocations = []
    for candidate in eligible:
        if remaining <= 0:
            break
        allocated_qty = min(candidate.allocatable_qty, remaining)
        allocations.append(
            AllocationSlice(
                sequence=len(allocations) + 1,
                lot_name=candidate.lot_name,
                warehouse=candidate.warehouse,
                source_method=candidate.source_method,
                relationship_model=candidate.relationship_model,
                qty=allocated_qty,
                serial_no=candidate.serial_no,
                batch_no=candidate.batch_no,
                fifo_datetime=candidate.fifo_datetime,
                receipt_name=candidate.receipt_name,
                receipt_row_index=candidate.receipt_row_index,
            )
        )
        remaining -= allocated_qty

    if remaining > 0:
        available = qty - remaining
        raise InsufficientStockError(
            f"Requested {qty} of {item_code}, but only {available} is allocatable at {location}"
        )
    return allocations
