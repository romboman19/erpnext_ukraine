from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def money(value: Any, precision: int = 2) -> Decimal:
    quantum = Decimal(1).scaleb(-precision)
    return decimal(value).quantize(quantum, rounding=ROUND_HALF_UP)


def percent(amount: Any, rate: Any, precision: int = 2) -> Decimal:
    return money(decimal(amount) * decimal(rate) / HUNDRED, precision)


def canonical_decimal(value: Any) -> str:
    value = decimal(value)
    return format(value, "f")
