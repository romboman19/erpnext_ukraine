from decimal import Decimal
from unittest import TestCase

from erpnext_consignment_and_commission.consignment_and_commission.services.tracking import (
    TRACKING_BATCH,
    TRACKING_NONE,
    TRACKING_SERIAL,
    ReceiptTrackingPolicy,
    TrackingValidationError,
    normalize_serial_numbers,
    validate_receipt_tracking,
)


class ReceiptTrackingTests(TestCase):
    def test_untracked_item_requires_no_selection(self) -> None:
        self.assertEqual(validate_receipt_tracking(self._policy()).tracking_type, TRACKING_NONE)

    def test_batch_accepts_existing_batch_or_auto_series(self) -> None:
        existing = validate_receipt_tracking(self._policy(has_batch_no=True, batch_no="BATCH-1"))
        automatic = validate_receipt_tracking(
            self._policy(has_batch_no=True, create_new_batch=True, batch_number_series="B-.#####")
        )
        self.assertEqual(existing.tracking_type, TRACKING_BATCH)
        self.assertEqual(automatic.tracking_type, TRACKING_BATCH)

    def test_batch_requires_explicit_or_automatic_identity(self) -> None:
        with self.assertRaisesRegex(TrackingValidationError, "auto-batch"):
            validate_receipt_tracking(self._policy(has_batch_no=True))

    def test_serial_input_is_normalized_and_counted(self) -> None:
        result = validate_receipt_tracking(
            self._policy(has_serial_no=True, stock_qty=Decimal("2"), serial_numbers="S-1\n\n S-2 ")
        )
        self.assertEqual(result.tracking_type, TRACKING_SERIAL)
        self.assertEqual(result.serial_numbers, ("S-1", "S-2"))

    def test_serial_requires_integral_quantity_and_exact_count(self) -> None:
        with self.assertRaisesRegex(TrackingValidationError, "whole number"):
            validate_receipt_tracking(self._policy(has_serial_no=True, stock_qty=Decimal("1.5")))
        with self.assertRaisesRegex(TrackingValidationError, "count"):
            validate_receipt_tracking(
                self._policy(has_serial_no=True, stock_qty=Decimal("2"), serial_numbers="S-1")
            )

    def test_serial_can_use_item_series_when_numbers_are_not_supplied(self) -> None:
        result = validate_receipt_tracking(
            self._policy(has_serial_no=True, stock_qty=Decimal("2"), serial_no_series="S-.#####")
        )
        self.assertEqual(result.tracking_type, TRACKING_SERIAL)
        self.assertEqual(result.serial_numbers, ())

    def test_duplicate_and_wrong_tracking_inputs_are_rejected(self) -> None:
        with self.assertRaisesRegex(TrackingValidationError, "unique"):
            normalize_serial_numbers("S-1\nS-1")
        with self.assertRaisesRegex(TrackingValidationError, "Batch-tracked"):
            validate_receipt_tracking(self._policy(batch_no="BATCH-1"))
        with self.assertRaisesRegex(TrackingValidationError, "both Batch and Serial"):
            validate_receipt_tracking(self._policy(has_batch_no=True, has_serial_no=True))

    @staticmethod
    def _policy(**overrides) -> ReceiptTrackingPolicy:
        values = {
            "stock_qty": Decimal("1"),
            "has_batch_no": False,
            "has_serial_no": False,
            "batch_no": None,
            "serial_numbers": None,
            "create_new_batch": False,
            "batch_number_series": None,
            "serial_no_series": None,
        }
        values.update(overrides)
        return ReceiptTrackingPolicy(**values)
