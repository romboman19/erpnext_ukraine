"""Frappe-independent Serial and Batch identity rules."""

from __future__ import annotations

from decimal import Decimal

from .domain import TRACKING_BATCH, TRACKING_NONE, TRACKING_SERIAL, GSFError


def split_serials(value: str | None) -> tuple[str, ...]:
    return tuple(line.strip() for line in (value or "").splitlines() if line.strip())


def single_serial(value: str | None) -> str | None:
    serials = split_serials(value)
    if len(serials) > 1:
        raise GSFError(
            "Each POS row must contain exactly one Serial No",
            "SERIAL_AMBIGUOUS",
        )
    return serials[0] if serials else None


def ordered_active_serials(
    configured: tuple[str, ...],
    active: set[str],
    *,
    actual_qty: Decimal,
    context: str,
) -> tuple[str, ...]:
    ordered = tuple(serial_no for serial_no in configured if serial_no in active)
    if Decimal(len(ordered)) != actual_qty:
        raise GSFError(
            f"{context} ledger quantity {actual_qty} does not match "
            f"its {len(ordered)} active Serial Nos",
            "SERIAL_AMBIGUOUS",
        )
    return ordered


def tracking_values(serial_no: str | None, batch_no: str | None) -> dict[str, str | int]:
    if serial_no:
        return {"use_serial_batch_fields": 1, "serial_no": serial_no}
    if batch_no:
        return {"use_serial_batch_fields": 1, "batch_no": batch_no}
    return {}


def return_tracking(serial_no: str | None, batch_no: str | None) -> tuple[str, str | None, str | None]:
    serials = split_serials(serial_no)
    if serials:
        return TRACKING_SERIAL, None, "\n".join(serials)
    if batch_no:
        return TRACKING_BATCH, batch_no, None
    return TRACKING_NONE, None, None
