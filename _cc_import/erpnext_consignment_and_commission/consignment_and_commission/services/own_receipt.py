"""Pure invariants for company-owned stock receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

OWN_SOURCE_METHODS = frozenset({"BUYOUT", "DEFERRED_PURCHASE"})


class OwnReceiptValidationError(ValueError):
    """Raised when an OWN receipt cannot safely create stock and debt."""


@dataclass(frozen=True, slots=True)
class OwnReceiptPolicy:
    source_method: str
    posting_date: date
    due_date: date | None
    currency: str
    company_currency: str
    conversion_rate: Decimal


@dataclass(frozen=True, slots=True)
class OwnReceiptLinePolicy:
    stock_qty: Decimal
    unit_rate: Decimal


def validate_own_receipt(policy: OwnReceiptPolicy) -> date:
    """Validate commercial timing and return the canonical payable due date."""
    if policy.source_method not in OWN_SOURCE_METHODS:
        raise OwnReceiptValidationError(f"Unsupported OWN source method: {policy.source_method}")
    if not policy.currency or not policy.company_currency:
        raise OwnReceiptValidationError("Receipt and Company currencies are required")

    rate = Decimal(str(policy.conversion_rate))
    if not rate.is_finite() or rate <= 0:
        raise OwnReceiptValidationError("Conversion rate must be finite and greater than zero")
    if policy.currency == policy.company_currency and rate != Decimal("1"):
        raise OwnReceiptValidationError("Company-currency receipts require conversion rate 1")

    if policy.source_method == "BUYOUT":
        if policy.due_date and policy.due_date != policy.posting_date:
            raise OwnReceiptValidationError("Buyout debt must be due on the receipt date")
        return policy.posting_date

    if not policy.due_date or policy.due_date <= policy.posting_date:
        raise OwnReceiptValidationError(
            "Deferred-purchase debt requires a due date after the receipt date"
        )
    return policy.due_date


def own_receipt_line_amount(policy: OwnReceiptLinePolicy) -> Decimal:
    qty = Decimal(str(policy.stock_qty))
    rate = Decimal(str(policy.unit_rate))
    if not qty.is_finite() or qty <= 0:
        raise OwnReceiptValidationError("Stock quantity must be finite and greater than zero")
    if not rate.is_finite() or rate <= 0:
        raise OwnReceiptValidationError("Purchase rate must be finite and greater than zero")
    return qty * rate
