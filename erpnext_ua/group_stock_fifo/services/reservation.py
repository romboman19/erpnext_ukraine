"""Pure reservation identity and lifecycle rules (§9.12, §13.3, §13.4).

Separate from the commission module's equivalent because the two genuinely
differ: GSF reserves for a *seller* against stock owned by other companies, so
its fingerprint carries coordinates CC has no concept of (checkout, posting
date, external row), and its lifecycle has the staging rungs CC does not need.
ADR-013 shares the *allocator*, not everything downstream of it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from .domain import GSFError

#: §12.2 — GSF stock is always some member company's own stock; the shared
#: allocator maps this method to the `OWN` relationship model.
GSF_SOURCE_METHOD = "GSF_LAYER"
GSF_RELATIONSHIP_MODEL = "OWN"

ALLOCATION_PENDING = "PENDING"
ALLOCATION_RESERVED = "RESERVED"
ALLOCATION_PREPARING = "PREPARING"
ALLOCATION_PREPARED = "PREPARED"
ALLOCATION_CONSUMED = "CONSUMED"
ALLOCATION_RELEASED = "RELEASED"
ALLOCATION_EXPIRED = "EXPIRED"
ALLOCATION_FAILED = "FAILED"
ALLOCATION_REVERSED = "REVERSED"

#: §9.12. `RESERVED → CONSUMED` is allowed without the staging rungs: a sale
#: that needs no reallocation never enters a stage, and forcing it through
#: `PREPARING` would make the audit trail claim work that never happened.
ALLOCATION_TRANSITIONS: dict[str, frozenset[str]] = {
    ALLOCATION_PENDING: frozenset({ALLOCATION_RESERVED, ALLOCATION_FAILED}),
    ALLOCATION_RESERVED: frozenset(
        {
            ALLOCATION_PREPARING,
            ALLOCATION_CONSUMED,
            ALLOCATION_RELEASED,
            ALLOCATION_EXPIRED,
            ALLOCATION_FAILED,
        }
    ),
    ALLOCATION_PREPARING: frozenset(
        {ALLOCATION_PREPARED, ALLOCATION_RELEASED, ALLOCATION_EXPIRED, ALLOCATION_FAILED}
    ),
    ALLOCATION_PREPARED: frozenset(
        {ALLOCATION_CONSUMED, ALLOCATION_RELEASED, ALLOCATION_EXPIRED, ALLOCATION_FAILED}
    ),
    ALLOCATION_CONSUMED: frozenset({ALLOCATION_REVERSED}),
    ALLOCATION_RELEASED: frozenset(),
    ALLOCATION_EXPIRED: frozenset(),
    ALLOCATION_FAILED: frozenset(),
    ALLOCATION_REVERSED: frozenset(),
}

#: Allocations that still hold stock against a layer. Anything else has
#: released its claim, so it must not count towards a position's reservations.
LIVE_ALLOCATION_STATUSES = (ALLOCATION_RESERVED, ALLOCATION_PREPARING, ALLOCATION_PREPARED)

#: §13.3: expiring after staging started leaves stock sitting in a lane, so it
#: needs compensation rather than a plain release. The distinction is recorded
#: here so callers cannot quietly treat the two the same.
STAGED_STATUSES = frozenset({ALLOCATION_PREPARING, ALLOCATION_PREPARED})

ALLOCATION_ID_PREFIX = "GSFA-"
SCOPE_LOCK_ID_PREFIX = "GSFSL-"


@dataclass(frozen=True, slots=True)
class ReservationRequest:
    """§13.4 — everything that makes two requests the same request."""

    idempotency_key: str
    company_group: str
    physical_location: str
    seller_company: str
    item_code: str
    qty: Decimal
    allowed_warehouses: frozenset[str]
    serial_no: str | None = None
    batch_no: str | None = None
    item_policy: str | None = None
    external_row_id: str | None = None
    posting_date: str | None = None
    checkout: str | None = None


def validate_reservation_request(request: ReservationRequest) -> None:
    key = request.idempotency_key
    if not key or key != key.strip() or len(key) > 140:
        raise GSFError(
            "Idempotency key must be 1-140 characters without edge whitespace",
            "IDEMPOTENCY_CONFLICT",
        )
    missing = [
        label
        for label, value in (
            ("Company Group", request.company_group),
            ("Physical Location", request.physical_location),
            ("Seller Company", request.seller_company),
            ("Item", request.item_code),
        )
        if not value
    ]
    if missing:
        raise GSFError(f"Missing reservation coordinates: {', '.join(missing)}", "GROUP_NOT_FOUND")
    qty = Decimal(str(request.qty))
    if not qty.is_finite() or qty <= 0:
        raise GSFError(
            "Reservation quantity must be finite and greater than zero", "INSUFFICIENT_GLOBAL_STOCK"
        )
    if not request.allowed_warehouses or any(not value for value in request.allowed_warehouses):
        raise GSFError("At least one non-empty source warehouse is required", "WAREHOUSE_BINDING_MISSING")
    if request.serial_no and qty != Decimal("1"):
        raise GSFError("An exact Serial reservation is for one unit", "SERIAL_AMBIGUOUS")
    if request.serial_no and request.batch_no:
        raise GSFError("A request names either a Serial or a Batch, not both", "BATCH_MISMATCH")


def reservation_fingerprint(request: ReservationRequest) -> str:
    """§13.4: reusing a key with a different fingerprint is a hard error, so the
    fingerprint has to cover every coordinate that changes what gets reserved."""
    validate_reservation_request(request)
    payload = {
        "company_group": request.company_group,
        "physical_location": request.physical_location,
        "seller_company": request.seller_company,
        "item_code": request.item_code,
        "qty": str(Decimal(str(request.qty)).normalize()),
        "allowed_warehouses": sorted(request.allowed_warehouses),
        "serial_no": request.serial_no,
        "batch_no": request.batch_no,
        "item_policy": request.item_policy,
        "external_row_id": request.external_row_id,
        "posting_date": request.posting_date,
        "checkout": request.checkout,
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def allocation_identity(*, idempotency_key: str, fingerprint: str) -> str:
    """A name derived from both, so a key reused with a new payload lands on a
    different row and collides on the unique index instead of overwriting."""
    digest = hashlib.sha256(f"{idempotency_key}:{fingerprint}".encode()).hexdigest()
    return ALLOCATION_ID_PREFIX + digest[:24].upper()


def scope_lock_identity(*, company_group: str, physical_location: str, item_code: str) -> str:
    """§13.2 level 2 — the row every reservation in one FIFO scope contends on."""
    digest = hashlib.sha256(
        "\x1f".join((company_group, physical_location, item_code)).encode()
    ).hexdigest()
    return SCOPE_LOCK_ID_PREFIX + digest[:24].upper()


def validate_allocation_transition(current: str, target: str) -> None:
    if current not in ALLOCATION_TRANSITIONS or target not in ALLOCATION_TRANSITIONS:
        raise GSFError(
            f"Unsupported allocation status transition {current} → {target}",
            "ALLOCATION_CONFLICT",
        )
    if current == target:
        return
    if target not in ALLOCATION_TRANSITIONS[current]:
        raise GSFError(
            f"GSF Allocation cannot move from {current} to {target}", "ALLOCATION_CONFLICT"
        )


def needs_compensation(status: str) -> bool:
    """§13.3: whether releasing from this status must compensate staged stock."""
    return status in STAGED_STATUSES
