"""Pure request identity for exact managed sale returns."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


class ManagedReturnError(ValueError):
    """Raised when an exact sold allocation cannot be returned safely."""


@dataclass(frozen=True, slots=True)
class ManagedReturnLine:
    sale_allocation: str
    qty: Decimal


@dataclass(frozen=True, slots=True)
class ManagedReturnRequest:
    idempotency_key: str
    posting_date: date
    lines: tuple[ManagedReturnLine, ...]


@dataclass(frozen=True, slots=True)
class ReturnFinancialDelta:
    """Exact incremental reversal derived from an immutable sold snapshot."""

    relationship_model: str
    qty: Decimal
    cumulative_qty: Decimal
    gross_amount: Decimal
    commission_amount: Decimal
    partner_amount: Decimal
    retained_amount: Decimal


def _decimal(value: Decimal | str | int | float, *, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ManagedReturnError(f"{label} must be a decimal number") from exc
    if not result.is_finite():
        raise ManagedReturnError(f"{label} must be finite")
    return result


def calculate_return_financial_delta(
    *,
    relationship_model: str,
    sold_qty: Decimal | str | int | float,
    returned_qty_before: Decimal | str | int | float,
    return_qty: Decimal | str | int | float,
    gross_amount: Decimal | str | int | float,
    commission_amount: Decimal | str | int | float,
    partner_amount: Decimal | str | int | float,
    currency_precision: int = 2,
) -> ReturnFinancialDelta:
    """Allocate rounding cumulatively so the final return exactly closes the sale."""
    if relationship_model not in {"OWN", "COMMISSION", "CONSIGNMENT"}:
        raise ManagedReturnError(f"Unsupported relationship model: {relationship_model}")
    if not 0 <= currency_precision <= 6:
        raise ManagedReturnError("Currency precision must be between 0 and 6")
    sold = _decimal(sold_qty, label="Sold quantity")
    previous_qty = _decimal(returned_qty_before, label="Previously returned quantity")
    quantity = _decimal(return_qty, label="Return quantity")
    gross_total = _decimal(gross_amount, label="Gross amount")
    commission_total = _decimal(commission_amount, label="Commission amount")
    partner_total = _decimal(partner_amount, label="Partner amount")
    cumulative_qty = previous_qty + quantity
    if sold <= 0 or previous_qty < 0 or quantity <= 0 or cumulative_qty > sold:
        raise ManagedReturnError("Return quantity is outside the remaining sold quantity")
    if gross_total <= 0 or commission_total < 0 or partner_total < 0:
        raise ManagedReturnError("Sale financial snapshot contains invalid amounts")
    if relationship_model == "OWN" and (commission_total or partner_total):
        raise ManagedReturnError("OWN sale cannot contain commission or partner debt")
    if relationship_model == "COMMISSION" and commission_total + partner_total != gross_total:
        raise ManagedReturnError("Commission sale financial snapshot is not balanced")
    if relationship_model == "CONSIGNMENT" and commission_total:
        raise ManagedReturnError("Consignment sale cannot contain commission income")

    quantum = Decimal("1").scaleb(-currency_precision)

    def cumulative_share(total: Decimal, qty: Decimal) -> Decimal:
        if qty == sold:
            return total
        return (total * qty / sold).quantize(quantum, rounding=ROUND_HALF_UP)

    gross = cumulative_share(gross_total, cumulative_qty) - cumulative_share(
        gross_total,
        previous_qty,
    )
    if relationship_model == "COMMISSION":
        commission = cumulative_share(commission_total, cumulative_qty) - cumulative_share(
            commission_total,
            previous_qty,
        )
        partner = gross - commission
    elif relationship_model == "CONSIGNMENT":
        commission = Decimal("0")
        partner = cumulative_share(partner_total, cumulative_qty) - cumulative_share(
            partner_total,
            previous_qty,
        )
    else:
        commission = Decimal("0")
        partner = Decimal("0")
    return ReturnFinancialDelta(
        relationship_model=relationship_model,
        qty=quantity,
        cumulative_qty=cumulative_qty,
        gross_amount=gross,
        commission_amount=commission,
        partner_amount=partner,
        retained_amount=gross - partner,
    )


def validate_return_request(request: ManagedReturnRequest) -> None:
    key = request.idempotency_key
    if not key or key != key.strip() or len(key) > 140:
        raise ManagedReturnError("Idempotency key must be 1-140 characters without edge whitespace")
    if not request.lines:
        raise ManagedReturnError("Managed return requires at least one sold allocation")
    names = [line.sale_allocation for line in request.lines]
    if any(not name for name in names) or len(set(names)) != len(names):
        raise ManagedReturnError("Managed return sold allocations must be non-empty and unique")
    for line in request.lines:
        qty = Decimal(str(line.qty))
        if not qty.is_finite() or qty <= 0:
            raise ManagedReturnError("Managed return quantity must be positive and finite")


def managed_return_fingerprint(request: ManagedReturnRequest) -> str:
    validate_return_request(request)
    payload = {
        "posting_date": request.posting_date.isoformat(),
        "lines": sorted(
            (
                {
                    "sale_allocation": line.sale_allocation,
                    "qty": str(Decimal(str(line.qty)).normalize()),
                }
                for line in request.lines
            ),
            key=lambda row: row["sale_allocation"],
        ),
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
