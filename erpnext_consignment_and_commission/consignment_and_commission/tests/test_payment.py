from datetime import date
from decimal import Decimal
from unittest import TestCase

from erpnext_consignment_and_commission.consignment_and_commission.services.payment import (
    SettlementPaymentError,
    SettlementPaymentRequest,
    payment_fingerprint,
    validate_payment_request,
)


class SettlementPaymentTests(TestCase):
    def request(self, **overrides: object) -> SettlementPaymentRequest:
        values = {
            "idempotency_key": "PAY-1",
            "settlement_report": "SET-1",
            "bank_account": "Bank - CCI",
            "amount": Decimal("25"),
            "posting_date": date(2026, 7, 31),
            "reference_no": "WIRE-1",
            "exchange_rate": Decimal("1"),
        }
        values.update(overrides)
        return SettlementPaymentRequest(**values)  # type: ignore[arg-type]

    def test_fingerprint_is_stable_and_amount_sensitive(self) -> None:
        self.assertEqual(payment_fingerprint(self.request()), payment_fingerprint(self.request()))
        self.assertNotEqual(
            payment_fingerprint(self.request()),
            payment_fingerprint(self.request(amount=Decimal("26"))),
        )

    def test_request_rejects_missing_coordinates_and_nonpositive_values(self) -> None:
        with self.assertRaisesRegex(SettlementPaymentError, "Bank Account"):
            validate_payment_request(self.request(bank_account=""))
        with self.assertRaisesRegex(SettlementPaymentError, "positive"):
            validate_payment_request(self.request(amount=Decimal("0")))
