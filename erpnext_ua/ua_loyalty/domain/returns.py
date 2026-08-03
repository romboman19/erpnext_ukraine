from __future__ import annotations

from decimal import Decimal

from .money import ZERO, decimal, money


def calculate_return_share(
    *,
    original_amount: Decimal,
    original_qty: Decimal,
    return_qty: Decimal,
    previous_return_qty: Decimal,
    previous_amount: Decimal,
    precision: int = 2,
) -> Decimal:
    original_amount = money(original_amount, precision)
    original_qty = decimal(original_qty)
    return_qty = decimal(return_qty)
    previous_return_qty = decimal(previous_return_qty)
    previous_amount = money(previous_amount, precision)
    if original_qty <= ZERO or return_qty <= ZERO:
        raise ValueError("Original and return quantities must be positive")
    if previous_return_qty < ZERO or previous_return_qty + return_qty > original_qty:
        raise ValueError("Return quantity exceeds the original quantity")
    if previous_amount < ZERO or previous_amount > original_amount:
        raise ValueError("Previous return allocation is invalid")
    if previous_return_qty + return_qty == original_qty:
        return money(original_amount - previous_amount, precision)
    return money(original_amount * return_qty / original_qty, precision)
