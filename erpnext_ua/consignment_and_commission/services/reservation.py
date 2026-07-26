"""Pure allocation-reservation identity and lifecycle invariants."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

ALLOCATION_STATUSES = frozenset({"PENDING", "RESERVED", "CONSUMED", "RELEASED", "EXPIRED"})
TERMINAL_ALLOCATION_STATUSES = frozenset({"CONSUMED", "RELEASED", "EXPIRED"})
ALLOWED_TRANSITIONS = {
    "PENDING": frozenset({"RESERVED"}),
    "RESERVED": TERMINAL_ALLOCATION_STATUSES,
    "CONSUMED": frozenset(),
    "RELEASED": frozenset(),
    "EXPIRED": frozenset(),
}


class ReservationError(ValueError):
    """Raised when reservation identity or lifecycle is invalid."""


@dataclass(frozen=True, slots=True)
class ReservationRequest:
    idempotency_key: str
    item_code: str
    company: str
    location: str
    qty: Decimal
    allowed_warehouses: frozenset[str]
    serial_no: str | None = None
    batch_no: str | None = None
    fiscal_policy: str | None = None


def validate_reservation_request(request: ReservationRequest) -> None:
    key = request.idempotency_key
    if not key or key != key.strip() or len(key) > 140:
        raise ReservationError("Idempotency key must be 1-140 characters without edge whitespace")
    missing = [
        label
        for label, value in (
            ("Item", request.item_code),
            ("Company", request.company),
            ("Location", request.location),
        )
        if not value
    ]
    if missing:
        raise ReservationError(f"Missing reservation coordinates: {', '.join(missing)}")
    qty = Decimal(str(request.qty))
    if not qty.is_finite() or qty <= 0:
        raise ReservationError("Reservation quantity must be finite and greater than zero")
    if not request.allowed_warehouses or any(not value for value in request.allowed_warehouses):
        raise ReservationError("At least one non-empty allowed Warehouse is required")
    if request.serial_no and qty != Decimal("1"):
        raise ReservationError("Exact Serial reservation quantity must equal one")


def reservation_fingerprint(request: ReservationRequest) -> str:
    validate_reservation_request(request)
    payload = {
        "item_code": request.item_code,
        "company": request.company,
        "location": request.location,
        "qty": str(Decimal(str(request.qty)).normalize()),
        "allowed_warehouses": sorted(request.allowed_warehouses),
        "serial_no": request.serial_no,
        "batch_no": request.batch_no,
        "fiscal_policy": request.fiscal_policy,
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_allocation_transition(current_status: str, target_status: str) -> None:
    if current_status not in ALLOCATION_STATUSES or target_status not in ALLOCATION_STATUSES:
        raise ReservationError("Unsupported allocation status transition")
    if current_status == target_status:
        return
    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        raise ReservationError(
            f"CC Allocation cannot transition from {current_status} to {target_status}"
        )
