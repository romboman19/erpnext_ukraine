"""Deterministic payment waterfall across legal Sales Invoice routes."""

from __future__ import annotations

from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def split_pos_payments(
    order: Any,
    route_totals: OrderedDict[str, Decimal],
    *,
    is_return: bool = False,
    fixed_route_payments: dict[str, list[dict]] | None = None,
) -> dict[str, list[dict]]:
    import frappe

    from erpnext_ua.ua_gift_certificates.adapters.accounting import invoice_payments

    precision = int(frappe.db.get_single_value("System Settings", "currency_precision") or 2)
    quantum = Decimal("1").scaleb(-precision)
    normalized_totals = OrderedDict(
        (
            route_id,
            Decimal(str(total)).quantize(quantum, rounding=ROUND_HALF_UP),
        )
        for route_id, total in route_totals.items()
    )
    source_payments = (
        invoice_payments(order, is_return=is_return)
        if fixed_route_payments is None
        else [
            {
                "mode_of_payment": row.mode_of_payment,
                "amount": (-1 if is_return else 1) * abs(Decimal(str(row.amount))),
            }
            for row in order.payments_plan
            if row.status == "Confirmed" and row.kind != "Gift Certificate"
        ]
    )
    payments = [
        {
            "mode_of_payment": row["mode_of_payment"],
            "remaining": abs(Decimal(str(row["amount"]))).quantize(
                quantum,
                rounding=ROUND_HALF_UP,
            ),
        }
        for row in source_payments
    ]
    fixed = _normalize_fixed(fixed_route_payments or {}, quantum=quantum, is_return=is_return)
    outstanding_by_route = OrderedDict()
    for route_id, total in normalized_totals.items():
        fixed_total = sum((abs(row["decimal_amount"]) for row in fixed.get(route_id, [])), Decimal("0"))
        if fixed_total > total:
            raise ValueError(f"Fixed POS payments exceed fulfillment route {route_id}")
        outstanding_by_route[route_id] = total - fixed_total
    if sum((row["remaining"] for row in payments), Decimal("0")) != sum(
        outstanding_by_route.values(), Decimal("0")
    ):
        raise ValueError("POS payment total does not match fulfillment route totals")

    result: dict[str, list[dict]] = {
        route_id: [
            {"mode_of_payment": row["mode_of_payment"], "amount": float(row["decimal_amount"])}
            for row in fixed.get(route_id, [])
        ]
        for route_id in normalized_totals
    }
    payment_index = 0
    for route_id, outstanding in outstanding_by_route.items():
        while outstanding > 0:
            if payment_index >= len(payments):
                raise ValueError(f"Route {route_id} remains unpaid by {outstanding}")
            payment = payments[payment_index]
            amount = min(outstanding, payment["remaining"])
            if amount > 0:
                result[route_id].append(
                    {
                        "mode_of_payment": payment["mode_of_payment"],
                        "amount": float(-amount if is_return else amount),
                    }
                )
                outstanding -= amount
                payment["remaining"] -= amount
            if payment["remaining"] == 0:
                payment_index += 1
    if any(row["remaining"] for row in payments):
        raise ValueError("POS payment waterfall left an unapplied amount")
    return result


def _normalize_fixed(
    payments: dict[str, list[dict]],
    *,
    quantum: Decimal,
    is_return: bool,
) -> dict[str, list[dict]]:
    sign = Decimal("-1") if is_return else Decimal("1")
    return {
        route_id: [
            {
                "mode_of_payment": row["mode_of_payment"],
                "decimal_amount": sign
                * abs(Decimal(str(row["amount"]))).quantize(quantum, rounding=ROUND_HALF_UP),
            }
            for row in rows
            if Decimal(str(row["amount"])) != 0
        ]
        for route_id, rows in payments.items()
    }
