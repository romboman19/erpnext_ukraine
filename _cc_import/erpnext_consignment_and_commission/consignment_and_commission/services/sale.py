"""Pure identity and validation rules for allocation-backed sales."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal


class ManagedSaleError(ValueError):
    """Raised when a managed Sales Invoice request is incomplete or unsafe."""


@dataclass(frozen=True, slots=True)
class ManagedSaleLine:
    allocation: str
    rate: Decimal


@dataclass(frozen=True, slots=True)
class ManagedSaleRequest:
    idempotency_key: str
    customer: str
    lines: tuple[ManagedSaleLine, ...]
    posting_date: str | None = None
    currency: str | None = None
    conversion_rate: Decimal | None = None
    pos_checkout: str | None = None
    pos_route: str | None = None
    pos_order: str | None = None


def validate_managed_sale_request(request: ManagedSaleRequest) -> None:
    key = request.idempotency_key
    if not key or key != key.strip() or len(key) > 140:
        raise ManagedSaleError(
            "Sale idempotency key must be 1-140 characters without edge whitespace"
        )
    if not request.customer:
        raise ManagedSaleError("Managed sale requires a Customer")
    if not request.lines:
        raise ManagedSaleError("Managed sale requires at least one allocation")
    if bool(request.currency) != (request.conversion_rate is not None):
        raise ManagedSaleError("Sale currency and conversion rate must be provided together")
    if request.conversion_rate is not None:
        conversion_rate = Decimal(str(request.conversion_rate))
        if not conversion_rate.is_finite() or conversion_rate <= 0:
            raise ManagedSaleError("Sale conversion rate must be positive and finite")
    pos_values = (request.pos_checkout, request.pos_route, request.pos_order)
    if any(pos_values) and not all(pos_values):
        raise ManagedSaleError("POS managed sale requires checkout, route and external order")
    allocations = [line.allocation for line in request.lines]
    if any(not value for value in allocations) or len(set(allocations)) != len(allocations):
        raise ManagedSaleError("Managed sale allocations must be non-empty and unique")
    for line in request.lines:
        rate = Decimal(str(line.rate))
        if not rate.is_finite() or rate < 0:
            raise ManagedSaleError("Managed sale rates must be finite and non-negative")


def managed_sale_fingerprint(request: ManagedSaleRequest) -> str:
    validate_managed_sale_request(request)
    payload = {
        "customer": request.customer,
        "posting_date": request.posting_date,
        "currency": request.currency,
        "conversion_rate": (
            str(Decimal(str(request.conversion_rate)).normalize())
            if request.conversion_rate is not None
            else None
        ),
        "pos_checkout": request.pos_checkout,
        "pos_route": request.pos_route,
        "pos_order": request.pos_order,
        "lines": sorted(
            (
                {
                    "allocation": line.allocation,
                    "rate": str(Decimal(str(line.rate)).normalize()),
                }
                for line in request.lines
            ),
            key=lambda row: row["allocation"],
        ),
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
