"""Pure Serial/Batch rules for Stage 2 tracked receipts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

TRACKING_NONE = "NONE"
TRACKING_BATCH = "BATCH"
TRACKING_SERIAL = "SERIAL"


class TrackingValidationError(ValueError):
    """Raised when tracked receipt input is ambiguous or incomplete."""


@dataclass(frozen=True, slots=True)
class ReceiptTrackingPolicy:
    stock_qty: Decimal
    has_batch_no: bool
    has_serial_no: bool
    batch_no: str | None
    serial_numbers: str | None
    create_new_batch: bool
    batch_number_series: str | None
    serial_no_series: str | None


@dataclass(frozen=True, slots=True)
class ReceiptTrackingResult:
    tracking_type: str
    serial_numbers: tuple[str, ...] = ()


def normalize_serial_numbers(value: str | None) -> tuple[str, ...]:
    serial_numbers = tuple(line.strip() for line in (value or "").splitlines() if line.strip())
    if len(set(serial_numbers)) != len(serial_numbers):
        raise TrackingValidationError("Serial numbers must be unique within one receipt row")
    return serial_numbers


def validate_receipt_tracking(policy: ReceiptTrackingPolicy) -> ReceiptTrackingResult:
    if policy.has_batch_no and policy.has_serial_no:
        raise TrackingValidationError("Items tracked by both Batch and Serial No are not supported yet")

    serial_numbers = normalize_serial_numbers(policy.serial_numbers)
    if not policy.has_batch_no and policy.batch_no:
        raise TrackingValidationError("Batch can be set only for a Batch-tracked Item")
    if not policy.has_serial_no and serial_numbers:
        raise TrackingValidationError("Serial numbers can be set only for a Serial-tracked Item")

    if policy.has_batch_no:
        if not policy.batch_no and not (policy.create_new_batch and policy.batch_number_series):
            raise TrackingValidationError(
                "Batch receipt requires an existing Batch or Item auto-batch series"
            )
        return ReceiptTrackingResult(TRACKING_BATCH)

    if policy.has_serial_no:
        integral_qty = policy.stock_qty.to_integral_value()
        if policy.stock_qty != integral_qty:
            raise TrackingValidationError("Serial-tracked receipt quantity must be a whole number")
        if serial_numbers and len(serial_numbers) != int(integral_qty):
            raise TrackingValidationError("Serial number count must equal the receipt stock quantity")
        if not serial_numbers and not policy.serial_no_series:
            raise TrackingValidationError(
                "Serial receipt requires explicit serial numbers or an Item serial number series"
            )
        return ReceiptTrackingResult(TRACKING_SERIAL, serial_numbers)

    return ReceiptTrackingResult(TRACKING_NONE)
