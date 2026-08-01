"""Frappe-independent POS return slice planning."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .domain import GSFError


@dataclass(frozen=True, slots=True)
class ReturnLine:
    """A quantity returned against one technical Sales Invoice Item."""

    sales_invoice_item: str
    qty: Decimal


def consume_return_rows(
    invoice_rows: dict[str, list[Any]],
    requested_rows: list[Any],
    prior: dict[str, Decimal],
) -> list[ReturnLine]:
    """Consume original technical rows in their stable invoice order."""
    result: list[ReturnLine] = []
    for requested in requested_rows:
        remaining = Decimal(str(requested.qty))
        rows = invoice_rows.get(requested.return_against_item, [])
        for row in rows:
            available = abs(Decimal(str(row.qty or 0))) - prior.get(row.name, Decimal("0"))
            take = min(remaining, max(available, Decimal("0")))
            if take > 0:
                result.append(ReturnLine(row.name, take))
                remaining -= take
            if remaining == 0:
                break
        if remaining > 0:
            returnable = Decimal(str(requested.qty)) - remaining
            raise GSFError(
                f"POS row {requested.return_against_item} has only {returnable} returnable",
                "MANUAL_REVIEW_REQUIRED",
            )
    return result
