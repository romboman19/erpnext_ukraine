from datetime import date
from decimal import Decimal
from unittest import TestCase

from erpnext_consignment_and_commission.consignment_and_commission.services.settlement import (
    SettlementError,
    SettlementRequest,
    calculate_reportable_partner_amount,
    calculate_reportable_partner_balance,
    settlement_fingerprint,
    validate_settlement_request,
)


class SettlementTests(TestCase):
    def request(self, **overrides: object) -> SettlementRequest:
        values = {
            "idempotency_key": "SETTLEMENT-1",
            "sale_allocations": ("SALE-1", "SALE-2"),
            "period_from": date(2026, 7, 1),
            "period_to": date(2026, 7, 31),
            "posting_date": date(2026, 7, 31),
        }
        values.update(overrides)
        return SettlementRequest(**values)  # type: ignore[arg-type]

    def test_identity_is_order_independent_but_payload_sensitive(self) -> None:
        first = self.request()
        reordered = self.request(sale_allocations=("SALE-2", "SALE-1"))
        changed = self.request(posting_date=date(2026, 8, 1))

        self.assertEqual(settlement_fingerprint(first), settlement_fingerprint(reordered))
        self.assertNotEqual(settlement_fingerprint(first), settlement_fingerprint(changed))

    def test_request_rejects_duplicates_and_invalid_dates(self) -> None:
        with self.assertRaisesRegex(SettlementError, "cannot be repeated"):
            validate_settlement_request(self.request(sale_allocations=("SALE-1", "SALE-1")))
        with self.assertRaisesRegex(SettlementError, "period start"):
            validate_settlement_request(
                self.request(
                    period_from=date(2026, 8, 1),
                    period_to=date(2026, 7, 31),
                )
            )
        with self.assertRaisesRegex(SettlementError, "posting date"):
            validate_settlement_request(self.request(posting_date=date(2026, 7, 30)))

    def test_returned_share_reduces_partner_obligation_with_currency_rounding(self) -> None:
        amount = calculate_reportable_partner_amount(
            sold_qty=Decimal("3"),
            returned_qty=Decimal("1"),
            partner_amount=Decimal("100"),
        )
        self.assertEqual(amount, Decimal("66.67"))

        with self.assertRaisesRegex(SettlementError, "valid bounds"):
            calculate_reportable_partner_amount(
                sold_qty=1,
                returned_qty=2,
                partner_amount=10,
            )

    def test_reportable_balance_uses_exact_immutable_return_reversals(self) -> None:
        self.assertEqual(
            calculate_reportable_partner_balance(
                partner_amount="85.00",
                reversed_partner_amount="28.33",
            ),
            Decimal("56.67"),
        )
        with self.assertRaisesRegex(SettlementError, "outside"):
            calculate_reportable_partner_balance(
                partner_amount="85.00",
                reversed_partner_amount="85.01",
            )
