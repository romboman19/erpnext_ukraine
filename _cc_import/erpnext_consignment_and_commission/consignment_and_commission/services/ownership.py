"""Pure plans for converting or returning third-party stock."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

RelationshipModel = Literal["COMMISSION", "CONSIGNMENT"]
MovementKind = Literal[
    "REMOVE_THIRD_PARTY_FOR_CONVERSION",
    "RECEIVE_OWN_BY_PURCHASE",
    "RETURN_TO_PARTNER",
]


class OwnershipPlanError(ValueError):
    """Raised when a conversion or partner-return plan violates an invariant."""


@dataclass(frozen=True, slots=True)
class OwnershipDispositionRequest:
    event_id: str
    item_code: str
    relationship_model: RelationshipModel
    source_lot: str
    source_warehouse: str
    available_qty: Decimal
    convert_qty: Decimal = Decimal("0")
    return_qty: Decimal = Decimal("0")
    target_lot: str | None = None
    target_warehouse: str | None = None
    unit_cost: Decimal = Decimal("0")
    currency: str = "UAH"
    company_currency: str = "UAH"
    exchange_rate: Decimal = Decimal("1")
    convert_serial_numbers: tuple[str, ...] = ()
    return_serial_numbers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OwnershipMovement:
    kind: MovementKind
    qty: Decimal
    source_lot: str | None
    source_warehouse: str | None
    target_lot: str | None
    target_warehouse: str | None
    serial_numbers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OwnershipDispositionPlan:
    event_id: str
    item_code: str
    relationship_model: RelationshipModel
    converted_qty: Decimal
    returned_qty: Decimal
    remaining_qty: Decimal
    obligation_amount: Decimal
    base_asset_value: Decimal
    currency: str
    company_currency: str
    exchange_rate: Decimal
    movements: tuple[OwnershipMovement, ...]


def _quantity(value: Decimal | str | int | float, *, label: str) -> Decimal:
    quantity = Decimal(str(value))
    if not quantity.is_finite() or quantity < 0:
        raise OwnershipPlanError(f"{label} must be a finite non-negative quantity")
    return quantity


def _positive_amount(value: Decimal | str | int | float, *, label: str) -> Decimal:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount <= 0:
        raise OwnershipPlanError(f"{label} must be finite and greater than zero")
    return amount


def _validate_serials(quantity: Decimal, serials: tuple[str, ...], *, label: str) -> None:
    if not serials:
        return
    if quantity != quantity.to_integral_value():
        raise OwnershipPlanError(f"{label} quantity must be whole when Serial Nos are selected")
    normalized = tuple(serial.strip() for serial in serials)
    if any(not serial for serial in normalized):
        raise OwnershipPlanError(f"{label} Serial Nos cannot be empty")
    if len(set(normalized)) != len(normalized):
        raise OwnershipPlanError(f"{label} Serial Nos must be unique")
    if Decimal(len(normalized)) != quantity:
        raise OwnershipPlanError(f"{label} Serial No count must equal its quantity")


def plan_ownership_disposition(request: OwnershipDispositionRequest) -> OwnershipDispositionPlan:
    """Plan an explicit third-party conversion and/or return without touching ledgers."""
    if not request.event_id or not request.item_code:
        raise OwnershipPlanError("Event ID and Item are required")
    if request.relationship_model not in {"COMMISSION", "CONSIGNMENT"}:
        raise OwnershipPlanError("Only commission or consignment stock can be converted")
    if not request.source_lot or not request.source_warehouse:
        raise OwnershipPlanError("Source ownership lot and warehouse are required")

    available = _quantity(request.available_qty, label="Available")
    converted = _quantity(request.convert_qty, label="Conversion")
    returned = _quantity(request.return_qty, label="Return")
    if available <= 0:
        raise OwnershipPlanError("Available quantity must be greater than zero")
    if converted + returned <= 0:
        raise OwnershipPlanError("At least one conversion or return quantity is required")
    if converted + returned > available:
        raise OwnershipPlanError("Conversion and return quantities exceed available third-party stock")

    _validate_serials(converted, request.convert_serial_numbers, label="Conversion")
    _validate_serials(returned, request.return_serial_numbers, label="Return")
    if set(request.convert_serial_numbers) & set(request.return_serial_numbers):
        raise OwnershipPlanError("The same Serial No cannot be converted and returned")

    movements: list[OwnershipMovement] = []
    obligation = Decimal("0")
    base_asset_value = Decimal("0")
    exchange_rate = _positive_amount(request.exchange_rate, label="Exchange rate")

    if converted:
        if not request.target_lot or not request.target_warehouse:
            raise OwnershipPlanError("Conversion target ownership lot and warehouse are required")
        if request.target_lot == request.source_lot:
            raise OwnershipPlanError("Conversion must create a distinct own-stock ownership lot")
        if request.target_warehouse == request.source_warehouse:
            raise OwnershipPlanError("Conversion must move stock into an own-stock warehouse")
        unit_cost = _positive_amount(request.unit_cost, label="Conversion unit cost")
        if not request.currency or not request.company_currency:
            raise OwnershipPlanError("Obligation and company currencies are required")
        obligation = converted * unit_cost
        base_asset_value = obligation * exchange_rate
        movements.extend(
            [
                OwnershipMovement(
                    kind="REMOVE_THIRD_PARTY_FOR_CONVERSION",
                    qty=converted,
                    source_lot=request.source_lot,
                    source_warehouse=request.source_warehouse,
                    target_lot=None,
                    target_warehouse=None,
                    serial_numbers=request.convert_serial_numbers,
                ),
                OwnershipMovement(
                    kind="RECEIVE_OWN_BY_PURCHASE",
                    qty=converted,
                    source_lot=None,
                    source_warehouse=None,
                    target_lot=request.target_lot,
                    target_warehouse=request.target_warehouse,
                    serial_numbers=request.convert_serial_numbers,
                ),
            ]
        )

    if returned:
        movements.append(
            OwnershipMovement(
                kind="RETURN_TO_PARTNER",
                qty=returned,
                source_lot=request.source_lot,
                source_warehouse=request.source_warehouse,
                target_lot=None,
                target_warehouse=None,
                serial_numbers=request.return_serial_numbers,
            )
        )

    return OwnershipDispositionPlan(
        event_id=request.event_id,
        item_code=request.item_code,
        relationship_model=request.relationship_model,
        converted_qty=converted,
        returned_qty=returned,
        remaining_qty=available - converted - returned,
        obligation_amount=obligation,
        base_asset_value=base_asset_value,
        currency=request.currency,
        company_currency=request.company_currency,
        exchange_rate=exchange_rate,
        movements=tuple(movements),
    )
