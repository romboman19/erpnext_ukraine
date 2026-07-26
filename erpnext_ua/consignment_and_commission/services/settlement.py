"""Pure settlement request identity and obligation calculations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


class SettlementError(ValueError):
    """Raised when a settlement report cannot be built safely."""


@dataclass(frozen=True, slots=True)
class SettlementRequest:
    idempotency_key: str
    sale_allocations: tuple[str, ...]
    period_from: date
    period_to: date
    posting_date: date


def validate_settlement_request(request: SettlementRequest) -> None:
    key = request.idempotency_key
    if not key or key != key.strip() or len(key) > 140:
        raise SettlementError("Idempotency key must be 1-140 characters without edge whitespace")
    if not request.sale_allocations or any(not value for value in request.sale_allocations):
        raise SettlementError("At least one CC Sale Allocation is required")
    if len(set(request.sale_allocations)) != len(request.sale_allocations):
        raise SettlementError("CC Sale Allocations cannot be repeated")
    if request.period_from > request.period_to:
        raise SettlementError("Settlement period start cannot be after its end")
    if request.posting_date < request.period_to:
        raise SettlementError("Settlement posting date cannot be before the period end")


def settlement_fingerprint(request: SettlementRequest) -> str:
    validate_settlement_request(request)
    payload = {
        "sale_allocations": sorted(request.sale_allocations),
        "period_from": request.period_from.isoformat(),
        "period_to": request.period_to.isoformat(),
        "posting_date": request.posting_date.isoformat(),
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def calculate_reportable_partner_amount(
    *,
    sold_qty: Decimal | str | int | float,
    returned_qty: Decimal | str | int | float,
    partner_amount: Decimal | str | int | float,
    currency_precision: int = 2,
) -> Decimal:
    """Return the unreturned share of an immutable sold-slice obligation."""
    try:
        sold = Decimal(str(sold_qty))
        returned = Decimal(str(returned_qty))
        partner = Decimal(str(partner_amount))
    except (InvalidOperation, ValueError) as exc:
        raise SettlementError("Settlement quantities and amount must be decimal numbers") from exc
    if not all(value.is_finite() for value in (sold, returned, partner)):
        raise SettlementError("Settlement quantities and amount must be finite")
    if sold <= 0 or returned < 0 or returned > sold or partner < 0:
        raise SettlementError("Settlement quantities or partner amount are outside valid bounds")
    if not 0 <= currency_precision <= 6:
        raise SettlementError("Currency precision must be between 0 and 6")
    quantum = Decimal("1").scaleb(-currency_precision)
    return (partner * (sold - returned) / sold).quantize(quantum, rounding=ROUND_HALF_UP)


def calculate_reportable_partner_balance(
    *,
    partner_amount: Decimal | str | int | float,
    reversed_partner_amount: Decimal | str | int | float,
    currency_precision: int = 2,
) -> Decimal:
    """Subtract immutable return audit amounts without introducing new rounding."""
    try:
        partner = Decimal(str(partner_amount))
        reversed_partner = Decimal(str(reversed_partner_amount))
    except (InvalidOperation, ValueError) as exc:
        raise SettlementError("Partner and reversed amounts must be decimal numbers") from exc
    if not partner.is_finite() or not reversed_partner.is_finite():
        raise SettlementError("Partner and reversed amounts must be finite")
    if partner < 0 or reversed_partner < 0 or reversed_partner > partner:
        raise SettlementError("Reversed partner amount is outside the original obligation")
    if not 0 <= currency_precision <= 6:
        raise SettlementError("Currency precision must be between 0 and 6")
    quantum = Decimal("1").scaleb(-currency_precision)
    return (partner - reversed_partner).quantize(quantum, rounding=ROUND_HALF_UP)
