"""Pure Stage 2 receipt and ownership-lot invariants."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

THIRD_PARTY_MODELS = frozenset({"COMMISSION", "CONSIGNMENT"})
STOCK_LOT_MODELS = THIRD_PARTY_MODELS | {"OWN"}
STOCK_LOT_SOURCE_METHODS = frozenset(
    {"BUYOUT", "DEFERRED_PURCHASE", "COMMISSION", "CONSIGNMENT"}
)
SOURCE_METHOD_MODEL = {
    "BUYOUT": "OWN",
    "DEFERRED_PURCHASE": "OWN",
    "COMMISSION": "COMMISSION",
    "CONSIGNMENT": "CONSIGNMENT",
}
LOT_STATUSES = frozenset({"PENDING", "OPEN", "BLOCKED", "EXHAUSTED", "CANCELLED"})


class ReceiptValidationError(ValueError):
    """Raised when a receipt or ownership lot violates a Stage 2 invariant."""


@dataclass(frozen=True, slots=True)
class ContractReceiptPolicy:
    status: str
    relationship_model: str
    valid_from: date
    valid_to: date | None
    posting_date: date


@dataclass(frozen=True, slots=True)
class ReceiptLinePolicy:
    item_code: str
    qty: Decimal
    uom: str
    stock_uom: str
    conversion_factor: Decimal
    is_stock_item: bool
    disabled: bool
    has_serial_no: bool
    has_batch_no: bool


@dataclass(frozen=True, slots=True)
class StockLotPolicy:
    relationship_model: str
    source_method: str
    received_qty: Decimal
    reserved_qty: Decimal
    lot_status: str


def _positive_decimal(value: Decimal | str | int | float, *, label: str) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0:
        raise ReceiptValidationError(f"{label} must be finite and greater than zero")
    return number


def validate_contract_for_receipt(policy: ContractReceiptPolicy) -> None:
    if policy.status != "ACTIVE":
        raise ReceiptValidationError("Receipt requires an Active CC Contract")
    if policy.relationship_model not in THIRD_PARTY_MODELS:
        raise ReceiptValidationError("Receipt supports only commission or consignment contracts")
    if policy.posting_date < policy.valid_from:
        raise ReceiptValidationError("Receipt date cannot precede the contract start date")
    if policy.valid_to and policy.posting_date > policy.valid_to:
        raise ReceiptValidationError("Receipt date cannot follow the contract end date")


def validate_receipt_line(policy: ReceiptLinePolicy) -> Decimal:
    if not policy.item_code:
        raise ReceiptValidationError("Receipt Item is required")
    if policy.disabled or not policy.is_stock_item:
        raise ReceiptValidationError("Receipt Item must be an enabled stock Item")
    qty = _positive_decimal(policy.qty, label="Receipt quantity")
    conversion_factor = _positive_decimal(policy.conversion_factor, label="Conversion factor")
    if policy.uom != policy.stock_uom or conversion_factor != Decimal("1"):
        raise ReceiptValidationError("Receipt Item quantity must use the Item stock UOM")
    return qty * conversion_factor


def receipt_warehouse(
    relationship_model: str,
    *,
    commission_warehouse: str,
    consignment_warehouse: str,
) -> str:
    warehouses = {
        "COMMISSION": commission_warehouse,
        "CONSIGNMENT": consignment_warehouse,
    }
    warehouse = warehouses.get(relationship_model)
    if not warehouse:
        raise ReceiptValidationError(f"No receipt warehouse configured for {relationship_model!r}")
    return warehouse


def validate_stock_lot(policy: StockLotPolicy) -> None:
    if policy.relationship_model not in STOCK_LOT_MODELS:
        raise ReceiptValidationError("CC Stock Lot has an unsupported relationship model")
    if policy.source_method not in STOCK_LOT_SOURCE_METHODS:
        raise ReceiptValidationError("CC Stock Lot has an unsupported source method")
    if SOURCE_METHOD_MODEL[policy.source_method] != policy.relationship_model:
        raise ReceiptValidationError(
            f"Source method {policy.source_method} requires relationship model "
            f"{SOURCE_METHOD_MODEL[policy.source_method]}"
        )
    received = _positive_decimal(policy.received_qty, label="Received quantity")
    reserved = Decimal(str(policy.reserved_qty))
    if not reserved.is_finite() or reserved < 0:
        raise ReceiptValidationError("Reserved quantity must be finite and non-negative")
    if reserved > received:
        raise ReceiptValidationError("Reserved quantity cannot exceed received quantity")
    if policy.lot_status not in LOT_STATUSES:
        raise ReceiptValidationError(f"Unsupported CC Stock Lot status: {policy.lot_status}")
