from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .money import ZERO, money


@dataclass(frozen=True, slots=True)
class BalanceState:
    marketing: Decimal
    pending: Decimal
    reserved: Decimal
    redeemable: Decimal
    debt: Decimal


@dataclass(frozen=True, slots=True)
class CreditResult:
    balance_before: Decimal
    credit: Decimal
    debt_offset: Decimal
    redeemable_credit: Decimal
    balance_after: Decimal


def balances(marketing: Decimal, pending: Decimal, reserved: Decimal) -> BalanceState:
    marketing = money(marketing)
    pending = money(pending)
    reserved = max(ZERO, money(reserved))
    return BalanceState(
        marketing=marketing,
        pending=pending,
        reserved=reserved,
        redeemable=money(max(ZERO, marketing - reserved)),
        debt=money(max(ZERO, -marketing)),
    )


def apply_credit(marketing: Decimal, credit: Decimal) -> CreditResult:
    marketing = money(marketing)
    credit = money(credit)
    if credit < ZERO:
        raise ValueError("Credit must not be negative")
    debt_offset = money(min(credit, max(ZERO, -marketing)))
    return CreditResult(
        balance_before=marketing,
        credit=credit,
        debt_offset=debt_offset,
        redeemable_credit=money(credit - debt_offset),
        balance_after=money(marketing + credit),
    )
