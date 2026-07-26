"""Pure identity and validation rules for persistent split POS checkout."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .pos_saga import PaymentTender


class POSCheckoutError(ValueError):
    """Raised when a persistent POS checkout request violates its contract."""


@dataclass(frozen=True, slots=True)
class POSRouteLine:
    allocation: str
    rate: Decimal
    external_row_id: str = ""


@dataclass(frozen=True, slots=True)
class POSRouteRequest:
    group_id: str
    company: str
    location: str
    legal_entity_type: str
    legal_entity_name: str
    fiscal_route: str
    lines: tuple[POSRouteLine, ...]


@dataclass(frozen=True, slots=True)
class POSCheckoutRequest:
    idempotency_key: str
    external_order_doctype: str
    external_order_name: str
    customer: str
    posting_date: date
    currency: str
    conversion_rate: Decimal
    fiscal_checkout: bool
    routes: tuple[POSRouteRequest, ...]
    tenders: tuple[PaymentTender, ...]
    lookup_token: str = ""


def validate_pos_checkout_request(request: POSCheckoutRequest) -> None:
    if (
        not request.idempotency_key
        or request.idempotency_key != request.idempotency_key.strip()
        or len(request.idempotency_key) > 140
    ):
        raise POSCheckoutError("POS checkout idempotency key must be 1-140 trimmed characters")
    if not all(
        (
            request.external_order_doctype,
            request.external_order_name,
            request.customer,
            request.posting_date,
            request.currency,
        )
    ):
        raise POSCheckoutError("POS checkout order, customer, date and currency are required")
    rate = Decimal(str(request.conversion_rate))
    if not rate.is_finite() or rate <= 0:
        raise POSCheckoutError("POS checkout conversion rate must be positive and finite")
    if not request.routes:
        raise POSCheckoutError("POS checkout requires at least one route")
    if not request.tenders:
        raise POSCheckoutError("POS checkout requires a payment plan")
    if len({route.group_id for route in request.routes}) != len(request.routes):
        raise POSCheckoutError("POS route group identifiers must be unique")
    allocations: list[str] = []
    for route in request.routes:
        if (
            not route.group_id
            or route.group_id != route.group_id.strip()
            or len(route.group_id) > 100
        ):
            raise POSCheckoutError(
                "POS route group identifiers must be 1-100 trimmed characters"
            )
        if route.fiscal_route not in {"FISCAL", "NON_FISCAL"}:
            raise POSCheckoutError(f"Unsupported fiscal route {route.fiscal_route!r}")
        if not all(
            (
                route.group_id,
                route.company,
                route.location,
                route.legal_entity_type,
                route.legal_entity_name,
            )
        ) or not route.lines:
            raise POSCheckoutError(f"POS route {route.group_id!r} is incomplete")
        for line in route.lines:
            value = Decimal(str(line.rate))
            if not line.allocation or not value.is_finite() or value < 0:
                raise POSCheckoutError(f"POS route {route.group_id!r} has an invalid line")
            allocations.append(line.allocation)
    if len(set(allocations)) != len(allocations):
        raise POSCheckoutError("One POS checkout cannot reuse an allocation across routes")
    if len({tender.tender_id for tender in request.tenders}) != len(request.tenders):
        raise POSCheckoutError("POS payment tender identifiers must be unique")
    if any(
        not tender.tender_id
        or not tender.mode_of_payment
        or not Decimal(str(tender.amount)).is_finite()
        or Decimal(str(tender.amount)) <= 0
        for tender in request.tenders
    ):
        raise POSCheckoutError("POS payment tenders must be complete and positive")


def pos_checkout_fingerprint(request: POSCheckoutRequest) -> str:
    validate_pos_checkout_request(request)

    def amount(value: Decimal) -> str:
        return str(Decimal(str(value)).normalize())

    payload = {
        "external_order_doctype": request.external_order_doctype,
        "external_order_name": request.external_order_name,
        "lookup_token": request.lookup_token,
        "customer": request.customer,
        "posting_date": request.posting_date.isoformat(),
        "currency": request.currency,
        "conversion_rate": amount(request.conversion_rate),
        "fiscal_checkout": bool(request.fiscal_checkout),
        "routes": sorted(
            (
                {
                    "group_id": route.group_id,
                    "company": route.company,
                    "location": route.location,
                    "legal_entity_type": route.legal_entity_type,
                    "legal_entity_name": route.legal_entity_name,
                    "fiscal_route": route.fiscal_route,
                    "lines": sorted(
                        (
                            {
                                "allocation": line.allocation,
                                "rate": amount(line.rate),
                                "external_row_id": line.external_row_id,
                            }
                            for line in route.lines
                        ),
                        key=lambda row: row["allocation"],
                    ),
                }
                for route in request.routes
            ),
            key=lambda row: row["group_id"],
        ),
        "tenders": sorted(
            (
                {
                    "tender_id": tender.tender_id,
                    "mode_of_payment": tender.mode_of_payment,
                    "amount": amount(tender.amount),
                }
                for tender in request.tenders
            ),
            key=lambda row: row["tender_id"],
        ),
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
