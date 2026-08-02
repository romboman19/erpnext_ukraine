"""Pure route manifest rules shared by every sales channel."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from hashlib import sha256

from .stock_domain import PlannedDomainSlice


@dataclass(frozen=True, slots=True)
class FulfillmentRouteKey:
    provider_id: str
    seller_company: str
    provider_location: str
    legal_entity_type: str
    legal_entity_name: str
    fiscal_route: str

    @property
    def stable_id(self) -> str:
        payload = "\x1f".join(asdict(self).values())
        return sha256(payload.encode()).hexdigest()[:20].upper()


@dataclass(frozen=True, slots=True)
class ProviderAllocationRef:
    route: FulfillmentRouteKey
    allocation_doctype: str
    allocation_name: str
    item_code: str
    qty: Decimal
    external_row_id: str | None
    rate: Decimal
    discount_amount: Decimal = Decimal("0")

    def as_dict(self) -> dict:
        values = asdict(self)
        values["route"] = asdict(self.route)
        for fieldname in ("qty", "rate", "discount_amount"):
            values[fieldname] = str(values[fieldname])
        return values


def route_key(row: PlannedDomainSlice) -> FulfillmentRouteKey:
    return FulfillmentRouteKey(
        provider_id=row.provider_id,
        seller_company=row.seller_company,
        provider_location=row.provider_location,
        legal_entity_type=row.legal_entity_type,
        legal_entity_name=row.legal_entity_name,
        fiscal_route=row.fiscal_route,
    )


def split_discount(
    total_discount: Decimal,
    quantities: list[Decimal],
) -> list[Decimal]:
    """Allocate one visible line discount without losing its rounding tail."""
    discount = Decimal(str(total_discount))
    total_qty = sum(quantities, Decimal("0"))
    if discount < 0 or total_qty <= 0:
        raise ValueError("Discount and route quantities are invalid")
    result: list[Decimal] = []
    allocated = Decimal("0")
    for index, qty in enumerate(quantities):
        share = discount - allocated if index == len(quantities) - 1 else discount * qty / total_qty
        result.append(share)
        allocated += share
    return result


def effective_rate(*, qty: Decimal, rate: Decimal, discount_amount: Decimal) -> Decimal:
    gross = Decimal(str(qty)) * Decimal(str(rate))
    discount = Decimal(str(discount_amount))
    if qty <= 0 or rate < 0 or discount < 0 or discount > gross:
        raise ValueError("Sale line rate or discount is invalid")
    return (gross - discount) / qty
