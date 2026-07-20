from datetime import date, time, timedelta
from decimal import Decimal
from unittest import TestCase

from ..services.partner_return import (
    PartnerReturnError,
    PartnerReturnRequest,
    canonical_posting_time,
    partner_return_fingerprint,
)


def _request(**overrides):
    values = {
        "idempotency_key": "partner-return-1",
        "posting_date": date(2026, 7, 14),
        "posting_time": time(9, 5),
        "source_lot": "CC-LOT-1",
        "qty": Decimal("1"),
        "reason": "Partner requested unsold stock",
        "serial_numbers": ("SER-1",),
    }
    values.update(overrides)
    return PartnerReturnRequest(**values)


class TestPartnerReturn(TestCase):
    def test_partner_return_fingerprint_is_canonical_and_payload_sensitive(self) -> None:
        first = partner_return_fingerprint(_request())
        same = partner_return_fingerprint(
            _request(qty=Decimal("1.0"), posting_time=timedelta(hours=9, minutes=5))
        )
        changed = partner_return_fingerprint(_request(qty=Decimal("2")))
        self.assertEqual(first, same)
        self.assertNotEqual(first, changed)

    def test_partner_return_rejects_invalid_identity_and_values(self) -> None:
        with self.assertRaisesRegex(PartnerReturnError, "idempotency key"):
            partner_return_fingerprint(_request(idempotency_key=" bad "))
        with self.assertRaisesRegex(PartnerReturnError, "positive and finite"):
            partner_return_fingerprint(_request(qty=Decimal("NaN")))
        with self.assertRaisesRegex(PartnerReturnError, "must be unique"):
            partner_return_fingerprint(_request(serial_numbers=("SER-1", "SER-1")))
        with self.assertRaisesRegex(PartnerReturnError, "posting time"):
            canonical_posting_time("25:00")
