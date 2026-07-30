"""Frappe-independent rules for the foundation layer (§28.3).

Everything here is a pure function over plain data so it can be unit-tested
without a site. The Frappe-facing controllers call these and supply the data.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

GSF_MANAGER_APP = "GSF"
OWN_POOL_ROLE = "GSF_OWN_POOL"
SALE_STAGE_ROLE = "GSF_SALE_STAGE"
RETURN_QUARANTINE_ROLE = "GSF_RETURN_QUARANTINE"

GSF_ROLES = frozenset({OWN_POOL_ROLE, SALE_STAGE_ROLE, RETURN_QUARANTINE_ROLE})

LANE_AVAILABLE = "AVAILABLE"
LANE_LOCKED = "LOCKED"
LANE_DIRTY = "DIRTY"
LANE_DISABLED = "DISABLED"

LAYER_PENDING = "PENDING"
LAYER_OPEN = "OPEN"
LAYER_BLOCKED = "BLOCKED"
LAYER_EXHAUSTED = "EXHAUSTED"
LAYER_CANCELLED = "CANCELLED"

TRACKING_NONE = "NONE"
TRACKING_BATCH = "BATCH"
TRACKING_SERIAL = "SERIAL"

LAYER_ID_PREFIX = "GSFL-"
BALANCE_ID_PREFIX = "GSFLB-"
# 32 hex characters of SHA-256. Long enough that a collision is not a real
# scenario, short enough that the whole name still reads in a link field.
LAYER_ID_DIGEST_LENGTH = 32


class GSFError(Exception):
    """Base domain error carrying a stable §33 code."""

    code = "MANUAL_REVIEW_REQUIRED"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


@dataclass(frozen=True, slots=True)
class WarehouseFacts:
    """What the registry needs to know about a warehouse to bind it."""

    name: str
    company: str
    is_group: bool
    disabled: bool


@dataclass(frozen=True, slots=True)
class BindingRequest:
    warehouse: WarehouseFacts
    company: str
    warehouse_role: str
    manager_app: str = GSF_MANAGER_APP
    existing_binding_app: str | None = None
    has_stock_movements: bool = False
    previous_role: str | None = None


def validate_binding(request: BindingRequest) -> None:
    """§8.2 exclusivity plus the §7.4 warehouse rules."""
    warehouse = request.warehouse
    if warehouse.is_group:
        raise GSFError(
            f"Warehouse {warehouse.name} is a group warehouse and cannot be a technical warehouse",
            "WAREHOUSE_DOMAIN_CONFLICT",
        )
    if warehouse.disabled:
        raise GSFError(f"Warehouse {warehouse.name} is disabled", "WAREHOUSE_BINDING_MISSING")
    if warehouse.company != request.company:
        raise GSFError(
            f"Warehouse {warehouse.name} belongs to {warehouse.company}, not {request.company}",
            "WAREHOUSE_DOMAIN_CONFLICT",
        )
    if request.existing_binding_app and request.existing_binding_app != request.manager_app:
        code = (
            "CC_WAREHOUSE_CONFLICT"
            if request.existing_binding_app == "CC"
            else "WAREHOUSE_DOMAIN_CONFLICT"
        )
        raise GSFError(
            f"Warehouse {warehouse.name} already belongs to stock domain "
            f"{request.existing_binding_app}. It cannot be registered as {request.warehouse_role}.",
            code,
        )
    if request.manager_app == GSF_MANAGER_APP and request.warehouse_role not in GSF_ROLES:
        raise GSFError(
            f"Unknown GSF warehouse role {request.warehouse_role}", "WAREHOUSE_DOMAIN_CONFLICT"
        )
    if request.has_stock_movements and request.previous_role and request.previous_role != request.warehouse_role:
        raise GSFError(
            f"Warehouse {warehouse.name} already carries stock as {request.previous_role} "
            f"and cannot be reassigned to {request.warehouse_role}",
            "WAREHOUSE_DOMAIN_CONFLICT",
        )


@dataclass(frozen=True, slots=True)
class GroupMemberFacts:
    company: str
    enabled: bool
    can_source_stock: bool
    can_sell_stock: bool
    base_currency: str


def validate_group(members: list[GroupMemberFacts], *, group_currency: str,
                   reporting_parent_company: str | None) -> None:
    """§9.3 validations: one currency, no duplicate members, parent is not a seller."""
    seen = set()
    for member in members:
        if member.company in seen:
            raise GSFError(f"Company {member.company} appears twice in the group", "GROUP_NOT_FOUND")
        seen.add(member.company)
        if member.enabled and member.base_currency != group_currency:
            raise GSFError(
                f"Company {member.company} has base currency {member.base_currency}, "
                f"group requires {group_currency}",
                "MANUAL_REVIEW_REQUIRED",
            )
    if reporting_parent_company and reporting_parent_company in seen:
        raise GSFError(
            f"Reporting parent {reporting_parent_company} cannot also be a trading member",
            "COMPANY_NOT_GROUP_MEMBER",
        )


@dataclass(frozen=True, slots=True)
class LaneFacts:
    lane_code: str
    status: str
    enabled: bool
    current_checkout: str | None = None
    non_zero_items: tuple[str, ...] = ()


def check_lane_available(lane: LaneFacts, *, checkout: str) -> None:
    """§9.8: zero balance is a precondition of the lock, not a formality."""
    if not lane.enabled or lane.status == LANE_DISABLED:
        raise GSFError(f"Staging lane {lane.lane_code} is disabled", "STAGE_LANE_BUSY")
    if lane.status == LANE_DIRTY:
        raise GSFError(
            f"Staging lane {lane.lane_code} is dirty and must be cleared by an operator",
            "STAGE_LANE_DIRTY",
        )
    if lane.status == LANE_LOCKED and lane.current_checkout != checkout:
        raise GSFError(
            f"Staging lane {lane.lane_code} is held by checkout {lane.current_checkout}",
            "STAGE_LANE_BUSY",
        )
    if lane.non_zero_items:
        raise GSFError(
            f"Staging lane {lane.lane_code} still holds {', '.join(lane.non_zero_items)}",
            "STAGE_LANE_DIRTY",
        )


@dataclass(frozen=True, slots=True)
class LayerOrigin:
    """The coordinates §11.3 hashes into a layer's identity.

    `origin_document` is an ERPNext document name, and gate 0e showed those are
    not stable across a delete (the naming series counter is rewound). That is
    tolerable *here* and only here: a layer whose origin document no longer
    exists must not exist either (§11.4), so a reused name can only ever collide
    with a layer that should already have been removed with it. Nothing else in
    GSF may key off an ERPNext name (ADR-014).
    """

    company_group: str
    origin_doctype: str
    origin_document: str
    origin_row_name: str
    item_code: str
    batch_no: str | None = None
    serial_numbers: tuple[str, ...] = ()


def layer_identity(origin: LayerOrigin, *, site_id: str) -> str:
    """§11.3 deterministic layer ID: reprocessing a document must land here again."""
    parts = (
        site_id,
        origin.company_group,
        origin.origin_doctype,
        origin.origin_document,
        origin.origin_row_name,
        origin.item_code,
        _tracking_identity(origin),
    )
    # Unit separator: it cannot occur in a Frappe name, so no combination of
    # values can be re-split into a different tuple with the same digest.
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
    return LAYER_ID_PREFIX + digest[:LAYER_ID_DIGEST_LENGTH]


def _tracking_identity(origin: LayerOrigin) -> str:
    if origin.serial_numbers:
        # Sorted, because the same physical serials received in a different row
        # order are the same layer identity.
        return "SERIAL:" + ",".join(sorted(origin.serial_numbers))
    if origin.batch_no:
        return "BATCH:" + origin.batch_no
    return TRACKING_NONE


LAYER_TRANSITIONS: dict[str, frozenset[str]] = {
    LAYER_PENDING: frozenset({LAYER_OPEN, LAYER_CANCELLED}),
    LAYER_OPEN: frozenset({LAYER_BLOCKED, LAYER_EXHAUSTED, LAYER_CANCELLED}),
    LAYER_BLOCKED: frozenset({LAYER_OPEN, LAYER_CANCELLED}),
    # A reversal can put quantity back into a layer that had run dry, so
    # EXHAUSTED is not terminal. CANCELLED is.
    LAYER_EXHAUSTED: frozenset({LAYER_OPEN}),
    LAYER_CANCELLED: frozenset(),
}


def validate_layer_transition(current: str, target: str) -> None:
    """A layer's status may only move along §9.9's lifecycle."""
    if current == target:
        return
    allowed = LAYER_TRANSITIONS.get(current)
    if allowed is None:
        raise GSFError(f"Unknown layer status {current}", "MANUAL_REVIEW_REQUIRED")
    if target not in allowed:
        raise GSFError(
            f"Layer status cannot move from {current} to {target}", "MANUAL_REVIEW_REQUIRED"
        )


#: §9.9 — frozen once the layer is OPEN, because global FIFO order and the
#: audit trail are both derived from them.
LAYER_IMMUTABLE_FIELDS = (
    "company_group",
    "item_code",
    "origin_company",
    "origin_doctype",
    "origin_document",
    "origin_row_name",
    "original_received_datetime",
    "tracking_type",
    "batch_no",
    "serial_numbers",
)


def check_layer_immutability(
    before: dict[str, object], after: dict[str, object], *, previous_status: str
) -> None:
    """§9.9: identity fields are writable while PENDING and frozen from OPEN on."""
    if previous_status == LAYER_PENDING:
        return
    changed = sorted(
        name for name in LAYER_IMMUTABLE_FIELDS if before.get(name) != after.get(name)
    )
    if changed:
        raise GSFError(
            f"Layer identity is immutable once OPEN; changed: {', '.join(changed)}",
            "MANUAL_REVIEW_REQUIRED",
        )


def validate_tracking_identity(
    *, tracking_type: str, batch_no: str | None, serial_numbers: tuple[str, ...], qty: float
) -> None:
    """§11.2: a tracked receipt must carry the exact identity it claims."""
    if tracking_type == TRACKING_BATCH:
        if not batch_no:
            raise GSFError("Batch-tracked layer has no batch", "BATCH_MISMATCH")
        if serial_numbers:
            raise GSFError("Batch-tracked layer carries serial numbers", "BATCH_MISMATCH")
    elif tracking_type == TRACKING_SERIAL:
        if not serial_numbers:
            raise GSFError("Serial-tracked layer has no serial numbers", "SERIAL_AMBIGUOUS")
        if len(set(serial_numbers)) != len(serial_numbers):
            raise GSFError("Serial numbers repeat within one layer", "SERIAL_AMBIGUOUS")
        if len(serial_numbers) != int(qty):
            raise GSFError(
                f"Layer carries {len(serial_numbers)} serials for a quantity of {qty}",
                "SERIAL_AMBIGUOUS",
            )
    elif tracking_type == TRACKING_NONE:
        if batch_no or serial_numbers:
            raise GSFError(
                "Untracked layer carries a batch or serial identity", "BATCH_MISMATCH"
            )
    else:
        raise GSFError(f"Unknown tracking type {tracking_type}", "MANUAL_REVIEW_REQUIRED")


#: §9.11 movement vocabulary. Kept closed on purpose: an unknown type in the
#: audit trail is indistinguishable from a bug that wrote the wrong one.
MOVEMENT_TYPES = frozenset(
    {
        "ORIGIN_RECEIPT",
        "OWN_POOL_TO_STAGE",
        "INTERCOMPANY_ISSUE",
        "INTERCOMPANY_RECEIPT",
        "SALE_CONSUMPTION",
        "SALE_RETURN",
        "PURCHASE_RETURN",
        "RECONCILIATION",
        "PHYSICAL_TRANSFER",
        "REVERSAL",
    }
)


@dataclass(frozen=True, slots=True)
class MovementFacts:
    movement_type: str
    qty: float
    idempotency_key: str
    is_reversal: bool = False
    reversal_of: str | None = None


def validate_movement(movement: MovementFacts) -> None:
    """§9.11: an immutable audit event has to be self-describing to be evidence."""
    if movement.movement_type not in MOVEMENT_TYPES:
        raise GSFError(
            f"Unknown movement type {movement.movement_type}", "MANUAL_REVIEW_REQUIRED"
        )
    if not movement.idempotency_key:
        raise GSFError("Movement has no idempotency key", "IDEMPOTENCY_CONFLICT")
    if not movement.qty:
        raise GSFError("Movement records no quantity", "MANUAL_REVIEW_REQUIRED")
    # A compensation may be typed either way — `REVERSAL`, or the natural
    # opposite leg flagged as one — but it must always name what it undoes.
    if movement.is_reversal != bool(movement.reversal_of):
        raise GSFError(
            "A reversal must name the movement it reverses, and only a reversal may",
            "MANUAL_REVIEW_REQUIRED",
        )


def balance_identity(*, stock_layer: str, company: str, warehouse: str) -> str:
    """§9.10 key. A layer can sit in several company/warehouse positions at once.

    Hashed rather than concatenated because company and warehouse names are
    user-chosen and together overflow a Frappe name long before they overflow
    anything else.
    """
    digest = hashlib.sha256("\x1f".join((stock_layer, company, warehouse)).encode()).hexdigest()
    return BALANCE_ID_PREFIX + digest[:LAYER_ID_DIGEST_LENGTH]


@dataclass(slots=True)
class ReadinessReport:
    """§30 readiness. The feature gate may only open when blocking is empty."""

    blocking_checks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.blocking_checks

    @property
    def status(self) -> str:
        return "ready_for_acceptance" if self.ready else "blocked"

    def block(self, message: str) -> None:
        self.blocking_checks.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "blocking_checks": list(self.blocking_checks),
            "warnings": list(self.warnings),
        }
