from datetime import date
from decimal import Decimal
from unittest import TestCase

from erpnext_ua.consignment_and_commission.services.own_receipt import (
    OwnReceiptLinePolicy,
    OwnReceiptPolicy,
    OwnReceiptValidationError,
    own_receipt_line_amount,
    validate_own_receipt,
)


class OwnReceiptPolicyTests(TestCase):
    def test_buyout_is_due_immediately(self) -> None:
        posting_date = date(2026, 7, 13)
        self.assertEqual(validate_own_receipt(self._receipt(posting_date=posting_date)), posting_date)

    def test_buyout_rejects_later_due_date(self) -> None:
        with self.assertRaisesRegex(OwnReceiptValidationError, "due on the receipt date"):
            validate_own_receipt(self._receipt(due_date=date(2026, 7, 14)))

    def test_deferred_purchase_requires_future_due_date(self) -> None:
        with self.assertRaisesRegex(OwnReceiptValidationError, "after the receipt date"):
            validate_own_receipt(self._receipt(source_method="DEFERRED_PURCHASE"))
        self.assertEqual(
            validate_own_receipt(
                self._receipt(source_method="DEFERRED_PURCHASE", due_date=date(2026, 8, 13))
            ),
            date(2026, 8, 13),
        )

    def test_company_currency_requires_rate_one(self) -> None:
        with self.assertRaisesRegex(OwnReceiptValidationError, "rate 1"):
            validate_own_receipt(self._receipt(conversion_rate=Decimal("1.1")))

    def test_foreign_currency_requires_positive_rate(self) -> None:
        with self.assertRaisesRegex(OwnReceiptValidationError, "greater than zero"):
            validate_own_receipt(
                self._receipt(currency="EUR", company_currency="UAH", conversion_rate=Decimal("0"))
            )

    def test_line_amount_requires_positive_finite_quantity_and_rate(self) -> None:
        self.assertEqual(
            own_receipt_line_amount(OwnReceiptLinePolicy(Decimal("2.5"), Decimal("40"))),
            Decimal("100.0"),
        )
        with self.assertRaisesRegex(OwnReceiptValidationError, "Purchase rate"):
            own_receipt_line_amount(OwnReceiptLinePolicy(Decimal("1"), Decimal("0")))

    @staticmethod
    def _receipt(**overrides: object) -> OwnReceiptPolicy:
        values = {
            "source_method": "BUYOUT",
            "posting_date": date(2026, 7, 13),
            "due_date": None,
            "currency": "UAH",
            "company_currency": "UAH",
            "conversion_rate": Decimal("1"),
        }
        values.update(overrides)
        return OwnReceiptPolicy(**values)  # type: ignore[arg-type]
