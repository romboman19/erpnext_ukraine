"""Frappe-independent rules for the foundation layer (§28.3).

Everything here is a pure function over plain data so it can be unit-tested
without a site. The Frappe-facing controllers call these and supply the data.
"""

from __future__ import annotations

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
