from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from .money import ZERO, money


@dataclass(frozen=True, slots=True)
class RedemptionLine:
    row: str
    limit: Decimal


@dataclass(frozen=True, slots=True)
class RedemptionAllocation:
    row: str
    amount: Decimal


def allocate_redemption(requested: Decimal, lines: Iterable[RedemptionLine]) -> tuple[RedemptionAllocation, ...]:
    remaining = max(ZERO, money(requested))
    result = []
    for line in lines:
        limit = max(ZERO, money(line.limit))
        allocated = money(min(remaining, limit))
        result.append(RedemptionAllocation(line.row, allocated))
        remaining = money(remaining - allocated)
    return tuple(result)
