"""Deterministic financial snapshots for one sold allocation slice."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


class SaleFinancialError(ValueError):
    """Raised when sale income or partner debt cannot be calculated safely."""


@dataclass(frozen=True, slots=True)
class SaleFinancialSnapshot:
    relationship_model: str
    qty: Decimal
    gross_amount: Decimal
    commission_rate: Decimal
    commission_amount: Decimal
    partner_unit_rate: Decimal
    partner_amount: Decimal
    retained_amount: Decimal


@dataclass(frozen=True, slots=True)
class BaseSaleFinancialSnapshot:
    conversion_rate: Decimal
    gross_amount: Decimal
    commission_amount: Decimal
    partner_amount: Decimal
    retained_amount: Decimal


def _decimal(value: Decimal | str | int | float | None, *, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SaleFinancialError(f"{label} must be a decimal number") from exc
    if not result.is_finite():
        raise SaleFinancialError(f"{label} must be finite")
    return result


def calculate_sale_financials(
    *,
    relationship_model: str,
    qty: Decimal | str | int | float,
    net_amount: Decimal | str | int | float,
    commission_rate: Decimal | str | int | float | None = None,
    partner_unit_rate: Decimal | str | int | float | None = None,
    currency_precision: int = 2,
    allow_negative_margin: bool = False,
) -> SaleFinancialSnapshot:
    """Snapshot gross income, retained income and partner obligation currency."""
    if relationship_model not in {"OWN", "COMMISSION", "CONSIGNMENT"}:
        raise SaleFinancialError(f"Unsupported relationship model: {relationship_model}")
    if not 0 <= currency_precision <= 6:
        raise SaleFinancialError("Currency precision must be between 0 and 6")
    quantity = _decimal(qty, label="Quantity")
    gross = _decimal(net_amount, label="Net sale amount")
    if quantity <= 0 or gross <= 0:
        raise SaleFinancialError("Quantity and net sale amount must be greater than zero")
    quantum = Decimal("1").scaleb(-currency_precision)
    gross = gross.quantize(quantum, rounding=ROUND_HALF_UP)

    rate = Decimal("0")
    commission = Decimal("0")
    partner_rate = Decimal("0")
    partner = Decimal("0")
    if relationship_model == "COMMISSION":
        rate = _decimal(commission_rate, label="Commission rate")
        if not Decimal("0") < rate <= Decimal("100"):
            raise SaleFinancialError("Commission rate must be above 0 and at most 100")
        commission = (gross * rate / Decimal("100")).quantize(
            quantum,
            rounding=ROUND_HALF_UP,
        )
        partner = gross - commission
        partner_rate = (partner / quantity).quantize(quantum, rounding=ROUND_HALF_UP)
    elif relationship_model == "CONSIGNMENT":
        partner_rate = _decimal(partner_unit_rate, label="Partner unit rate")
        if partner_rate <= 0:
            raise SaleFinancialError("Consignment partner unit rate must be greater than zero")
        partner = (partner_rate * quantity).quantize(quantum, rounding=ROUND_HALF_UP)

    retained = gross - partner
    if retained < 0 and not allow_negative_margin:
        raise SaleFinancialError("Partner amount above net sale amount requires loss approval")
    return SaleFinancialSnapshot(
        relationship_model=relationship_model,
        qty=quantity,
        gross_amount=gross,
        commission_rate=rate,
        commission_amount=commission,
        partner_unit_rate=partner_rate,
        partner_amount=partner,
        retained_amount=retained,
    )


def convert_sale_financials_to_base(
    financials: SaleFinancialSnapshot,
    *,
    conversion_rate: Decimal | str | int | float,
    currency_precision: int = 2,
) -> BaseSaleFinancialSnapshot:
    """Convert a balanced sale snapshot while assigning every rounding residual."""
    if not 0 <= currency_precision <= 6:
        raise SaleFinancialError("Currency precision must be between 0 and 6")
    rate = _decimal(conversion_rate, label="Conversion rate")
    if rate <= 0:
        raise SaleFinancialError("Conversion rate must be greater than zero")
    quantum = Decimal("1").scaleb(-currency_precision)

    def converted(value: Decimal) -> Decimal:
        return (value * rate).quantize(quantum, rounding=ROUND_HALF_UP)

    gross = converted(financials.gross_amount)
    if financials.relationship_model == "COMMISSION":
        commission = converted(financials.commission_amount)
        partner = gross - commission
    elif financials.relationship_model == "CONSIGNMENT":
        commission = Decimal("0")
        partner = converted(financials.partner_amount)
    else:
        commission = Decimal("0")
        partner = Decimal("0")
    return BaseSaleFinancialSnapshot(
        conversion_rate=rate,
        gross_amount=gross,
        commission_amount=commission,
        partner_amount=partner,
        retained_amount=gross - partner,
    )
