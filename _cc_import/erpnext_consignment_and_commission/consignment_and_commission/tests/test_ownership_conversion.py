from datetime import date, time, timedelta
from decimal import Decimal
from unittest import TestCase

from ..services.ownership_conversion import (
    OwnershipConversionError,
    OwnershipConversionRequest,
    ownership_conversion_fingerprint,
)


def _request(**overrides):
    values = {
        "idempotency_key": "conversion-1",
        "posting_date": date(2026, 7, 14),
        "posting_time": time(10, 15),
        "source_lot": "CC-LOT-1",
        "qty": Decimal("2"),
        "source_method": "DEFERRED_PURCHASE",
        "unit_cost": Decimal("80"),
        "currency": "UAH",
        "exchange_rate": Decimal("1"),
        "reason": "Purchase accepted unsold stock",
        "due_date": date(2026, 7, 21),
        "serial_numbers": ("SER-1", "SER-2"),
    }
    values.update(overrides)
    return OwnershipConversionRequest(**values)


class TestOwnershipConversion(TestCase):
    def test_conversion_fingerprint_normalizes_time_money_and_serial_order(self) -> None:
        first = ownership_conversion_fingerprint(_request())
        same = ownership_conversion_fingerprint(
            _request(
                posting_time=timedelta(hours=10, minutes=15),
                qty=Decimal("2.0"),
                unit_cost=Decimal("80.00"),
                serial_numbers=("SER-2", "SER-1"),
            )
        )
        changed = ownership_conversion_fingerprint(_request(unit_cost=Decimal("81")))
        self.assertEqual(first, same)
        self.assertNotEqual(first, changed)

    def test_conversion_request_rejects_invalid_terms(self) -> None:
        with self.assertRaisesRegex(OwnershipConversionError, "requires a due date"):
            ownership_conversion_fingerprint(_request(due_date=None))
        with self.assertRaisesRegex(OwnershipConversionError, "cannot set a due date"):
            ownership_conversion_fingerprint(_request(source_method="BUYOUT"))
        with self.assertRaisesRegex(OwnershipConversionError, "positive and finite"):
            ownership_conversion_fingerprint(_request(unit_cost=Decimal("NaN")))
        with self.assertRaisesRegex(OwnershipConversionError, "must be unique"):
            ownership_conversion_fingerprint(_request(serial_numbers=("SER-1", "SER-1")))
