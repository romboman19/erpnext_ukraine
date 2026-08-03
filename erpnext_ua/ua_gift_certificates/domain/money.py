from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

ZERO = Decimal("0.00")
ONE_HUNDRED = Decimal("100")
QUANTUM = Decimal("0.01")


def decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def money(value: Any) -> Decimal:
    return decimal(value).quantize(QUANTUM, rounding=ROUND_HALF_UP)


def canonical(value: Any) -> str:
    return format(money(value), ".2f")


def require_non_negative(value: Any, fieldname: str) -> Decimal:
    amount = money(value)
    if amount < ZERO:
        raise ValueError(f"{fieldname} must not be negative")
    return amount
