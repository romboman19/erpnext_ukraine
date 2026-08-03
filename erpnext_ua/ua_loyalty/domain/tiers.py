from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from .money import ZERO, decimal


@dataclass(frozen=True, slots=True)
class Tier:
    code: str
    threshold: Decimal
    rate: Decimal


NO_TIER = Tier("BELOW_THRESHOLD", ZERO, ZERO)


def select_tier(metric: Decimal, tiers: Iterable[Tier]) -> Tier:
    metric = decimal(metric)
    selected = NO_TIER
    previous = None
    for tier in sorted(tiers, key=lambda row: row.threshold):
        threshold = decimal(tier.threshold)
        if previous is not None and threshold <= previous:
            raise ValueError("Tier thresholds must be strictly increasing")
        if decimal(tier.rate) < ZERO:
            raise ValueError("Tier rate must not be negative")
        previous = threshold
        if threshold <= metric:
            selected = Tier(tier.code, threshold, decimal(tier.rate))
    return selected
