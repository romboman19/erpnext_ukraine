"""Exact monetary shares for benefits split across legal fulfillment routes."""

from __future__ import annotations

from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def route_quantities(checkout: Any, external_row_id: str) -> OrderedDict[str, Decimal]:
    from .fulfillment_reservation import checkout_refs

    result: OrderedDict[str, Decimal] = OrderedDict()
    for ref in checkout_refs(checkout):
        if ref.external_row_id != external_row_id:
            continue
        route_id = ref.route.stable_id
        result[route_id] = result.get(route_id, Decimal("0")) + ref.qty
    return result


def split_route_amount(
    total: Decimal,
    quantities: OrderedDict[str, Decimal],
    *,
    quantum: Decimal = Decimal("0.01"),
) -> OrderedDict[str, Decimal]:
    """Round every route except the final one, which receives the exact tail."""
    amount = Decimal(str(total)).quantize(quantum, rounding=ROUND_HALF_UP)
    total_qty = sum(quantities.values(), Decimal("0"))
    if amount < 0 or total_qty <= 0 or any(qty <= 0 for qty in quantities.values()):
        raise ValueError("Fulfillment benefit amount or route quantities are invalid")
    result: OrderedDict[str, Decimal] = OrderedDict()
    allocated = Decimal("0")
    rows = list(quantities.items())
    for index, (route_id, qty) in enumerate(rows):
        share = (
            amount - allocated
            if index == len(rows) - 1
            else (amount * qty / total_qty).quantize(quantum, rounding=ROUND_HALF_UP)
        )
        if share < 0:
            raise ValueError("Fulfillment benefit rounding produced a negative route share")
        result[route_id] = share
        allocated += share
    if allocated != amount:
        raise ValueError("Fulfillment benefit route shares do not reconcile")
    return result


def route_amount(
    checkout: Any,
    *,
    external_row_id: str,
    route_id: str,
    total: Decimal,
    quantum: Decimal = Decimal("0.01"),
) -> Decimal:
    quantities = route_quantities(checkout, external_row_id)
    if route_id not in quantities:
        raise ValueError(f"Fulfillment route {route_id} has no row {external_row_id}")
    return split_route_amount(total, quantities, quantum=quantum)[route_id]
