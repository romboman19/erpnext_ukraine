from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .money import ZERO, money


@dataclass(frozen=True)
class EligibleLine:
    row_name: str
    amount: Decimal


@dataclass(frozen=True)
class Allocation:
    row_name: str
    amount: Decimal


def allocate_proportionally(amount, lines: list[EligibleLine]) -> list[Allocation]:
    target = money(amount)
    positive = [EligibleLine(line.row_name, money(line.amount)) for line in lines if money(line.amount) > ZERO]
    capacity = money(sum((line.amount for line in positive), ZERO))
    if target < ZERO or target > capacity:
        raise ValueError("allocation exceeds eligible capacity")
    if not positive or target == ZERO:
        return []
    result: list[Allocation] = []
    allocated = ZERO
    for index, line in enumerate(positive):
        share = money(target - allocated) if index == len(positive) - 1 else money(target * line.amount / capacity)
        share = min(share, line.amount, money(target - allocated))
        result.append(Allocation(line.row_name, share))
        allocated = money(allocated + share)
    residual = money(target - allocated)
    if residual:
        last = result[-1]
        if money(last.amount + residual) > positive[-1].amount:
            raise ValueError("rounding residual exceeds line capacity")
        result[-1] = Allocation(last.row_name, money(last.amount + residual))
    return result


def restore_share(
    original_amount,
    returned_qty,
    sold_qty,
    already_restored=ZERO,
    already_returned_qty=Decimal("0"),
) -> Decimal:
    original = money(original_amount)
    returned = Decimal(str(returned_qty))
    sold = Decimal(str(sold_qty))
    restored = money(already_restored)
    prior_qty = Decimal(str(already_returned_qty))
    if sold <= 0 or returned < 0 or returned > sold:
        raise ValueError("invalid return quantity")
    if prior_qty < 0 or prior_qty + returned > sold:
        raise ValueError("cumulative return quantity exceeds sold quantity")
    if prior_qty + returned == sold:
        return money(original - restored)
    return min(money(original * returned / sold), money(original - restored))
