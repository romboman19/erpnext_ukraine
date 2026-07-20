from dataclasses import dataclass
from datetime import date
from decimal import Decimal

RELATIONSHIP_MODELS = frozenset({"COMMISSION", "CONSIGNMENT"})
PARTNER_RELATIONSHIP_MODELS = RELATIONSHIP_MODELS | {"BOTH"}
CONTRACT_STATUSES = frozenset({"DRAFT", "ACTIVE", "SUSPENDED", "CLOSED"})
FISCAL_POLICIES = frozenset({"AUTO", "FISCAL", "NON_FISCAL"})
PRICE_AUTHORITIES = frozenset({"COMPANY", "PARTNER", "CONTRACT"})


class FoundationValidationError(ValueError):
    """Raised when Stage 1 foundation invariants are violated."""


@dataclass(frozen=True, slots=True)
class SettingsPolicy:
    enable_commission: bool
    enable_consignment: bool
    reservation_ttl_minutes: int
    allocation_retry_limit: int
    enable_buyout: bool = False
    enable_deferred_purchase: bool = False


@dataclass(frozen=True, slots=True)
class LocationPolicy:
    company: str
    legal_entity_type: str
    legal_entity_name: str
    own_warehouse: str
    commission_warehouse: str
    consignment_warehouse: str


@dataclass(frozen=True, slots=True)
class ContractPolicy:
    relationship_model: str
    status: str
    valid_from: date
    valid_to: date | None
    commission_rate: Decimal
    settlement_deadline_days: int
    fiscal_policy: str
    price_authority: str


def validate_settings_policy(policy: SettingsPolicy) -> None:
    if not any(
        (
            policy.enable_buyout,
            policy.enable_deferred_purchase,
            policy.enable_commission,
            policy.enable_consignment,
        )
    ):
        raise FoundationValidationError("At least one stock source method must be enabled")
    if not 1 <= policy.reservation_ttl_minutes <= 1_440:
        raise FoundationValidationError("Reservation TTL must be between 1 and 1440 minutes")
    if not 1 <= policy.allocation_retry_limit <= 10:
        raise FoundationValidationError("Allocation retry limit must be between 1 and 10")


def validate_location_policy(policy: LocationPolicy) -> None:
    required_values = {
        "company": policy.company,
        "legal entity type": policy.legal_entity_type,
        "legal entity name": policy.legal_entity_name,
        "own warehouse": policy.own_warehouse,
        "commission warehouse": policy.commission_warehouse,
        "consignment warehouse": policy.consignment_warehouse,
    }
    missing = [label for label, value in required_values.items() if not value]
    if missing:
        raise FoundationValidationError(f"Missing required location values: {', '.join(missing)}")

    warehouses = (policy.own_warehouse, policy.commission_warehouse, policy.consignment_warehouse)
    if len(set(warehouses)) != len(warehouses):
        raise FoundationValidationError("OWN, COMMISSION and CONSIGNMENT warehouses must be distinct")


def validate_partner_relationship_model(model: str) -> None:
    if model not in PARTNER_RELATIONSHIP_MODELS:
        raise FoundationValidationError(f"Unsupported partner relationship model: {model}")


def partner_allows_relationship(partner_model: str, contract_model: str) -> bool:
    validate_partner_relationship_model(partner_model)
    if contract_model not in RELATIONSHIP_MODELS:
        raise FoundationValidationError(f"Unsupported contract relationship model: {contract_model}")
    return partner_model == "BOTH" or partner_model == contract_model


def validate_contract_policy(policy: ContractPolicy) -> None:
    if policy.relationship_model not in RELATIONSHIP_MODELS:
        raise FoundationValidationError(f"Unsupported relationship model: {policy.relationship_model}")
    if policy.status not in CONTRACT_STATUSES:
        raise FoundationValidationError(f"Unsupported contract status: {policy.status}")
    if policy.valid_to and policy.valid_to < policy.valid_from:
        raise FoundationValidationError("Contract end date cannot be before its start date")
    if not 0 <= policy.settlement_deadline_days <= 3_650:
        raise FoundationValidationError("Settlement deadline must be between 0 and 3650 days")
    if policy.fiscal_policy not in FISCAL_POLICIES:
        raise FoundationValidationError(f"Unsupported fiscal policy: {policy.fiscal_policy}")
    if policy.price_authority not in PRICE_AUTHORITIES:
        raise FoundationValidationError(f"Unsupported price authority: {policy.price_authority}")

    if policy.relationship_model == "COMMISSION":
        if not Decimal("0") < policy.commission_rate <= Decimal("100"):
            raise FoundationValidationError("Commission contracts require a rate above 0 and at most 100")
    elif policy.commission_rate != Decimal("0"):
        raise FoundationValidationError("Consignment contracts cannot define a commission rate")


def date_ranges_overlap(
    left_from: date,
    left_to: date | None,
    right_from: date,
    right_to: date | None,
) -> bool:
    maximum = date.max
    return left_from <= (right_to or maximum) and right_from <= (left_to or maximum)
