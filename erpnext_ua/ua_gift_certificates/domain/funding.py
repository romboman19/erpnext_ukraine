from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .money import ZERO, money, require_non_negative


@dataclass(frozen=True)
class Funding:
    paid: Decimal
    promotional: Decimal
    premium: Decimal

    @property
    def balance(self) -> Decimal:
        return money(self.paid + self.promotional)


def initial_funding(face_value, sale_price) -> Funding:
    face = require_non_negative(face_value, "face_value")
    price = require_non_negative(sale_price, "sale_price")
    paid = min(face, price)
    return Funding(paid=paid, promotional=money(max(face - paid, ZERO)), premium=money(max(price - face, ZERO)))


def split_consumption(paid_balance, promotional_balance, amount, policy: str = "Proportional") -> Funding:
    paid = require_non_negative(paid_balance, "paid_balance")
    promotional = require_non_negative(promotional_balance, "promotional_balance")
    requested = require_non_negative(amount, "amount")
    total = money(paid + promotional)
    if requested > total:
        raise ValueError("amount exceeds component balance")
    if requested == ZERO:
        return Funding(ZERO, ZERO, ZERO)
    if policy == "Paid First":
        paid_part = min(paid, requested)
    elif policy == "Promotional First":
        paid_part = max(ZERO, requested - promotional)
    else:
        paid_part = money(requested * paid / total) if total else ZERO
        paid_part = min(paid_part, paid, requested)
    promotional_part = money(requested - paid_part)
    if promotional_part > promotional:
        promotional_part = promotional
        paid_part = money(requested - promotional_part)
    return Funding(paid=paid_part, promotional=promotional_part, premium=ZERO)
