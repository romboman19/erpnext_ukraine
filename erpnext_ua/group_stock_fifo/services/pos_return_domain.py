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
        rows = _matching_rows(
            invoice_rows.get(requested.return_against_item, []),
            requested,
        )
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


def _matching_rows(rows: list[Any], requested: Any) -> list[Any]:
    requested_serials = _serials(getattr(requested, "serial_no", None))
    tracked_serials = {
        serial_no
        for row in rows
        for serial_no in _serials(getattr(row, "serial_no", None))
    }
    if tracked_serials:
        if len(requested_serials) != 1:
            raise GSFError(
                f"POS row {requested.return_against_item} requires its exact Serial No",
                "SERIAL_AMBIGUOUS",
            )
        if requested_serials[0] not in tracked_serials:
            raise GSFError(
                f"Serial No {requested_serials[0]} was not sold in POS row "
                f"{requested.return_against_item}",
                "SERIAL_AMBIGUOUS",
            )
        return [
            row
            for row in rows
            if requested_serials[0] in _serials(getattr(row, "serial_no", None))
        ]

    requested_batch = getattr(requested, "batch_no", None)
    tracked_batches = {getattr(row, "batch_no", None) for row in rows}
    tracked_batches.discard(None)
    if tracked_batches and requested_batch:
        if requested_batch not in tracked_batches:
            raise GSFError(
                f"Batch {requested_batch} was not sold in POS row {requested.return_against_item}",
                "BATCH_MISMATCH",
            )
        return [row for row in rows if getattr(row, "batch_no", None) == requested_batch]
    return rows


def _serials(value: str | None) -> tuple[str, ...]:
    return tuple(line.strip() for line in (value or "").splitlines() if line.strip())
