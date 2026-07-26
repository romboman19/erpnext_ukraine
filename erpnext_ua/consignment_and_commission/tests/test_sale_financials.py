from decimal import Decimal
from unittest import TestCase

from erpnext_ua.consignment_and_commission.services.sale_financials import (
    SaleFinancialError,
    calculate_sale_financials,
    convert_sale_financials_to_base,
)


class SaleFinancialTests(TestCase):
    def test_foreign_commission_conversion_assigns_rounding_residual(self) -> None:
        financials = calculate_sale_financials(
            relationship_model="COMMISSION",
            qty=3,
            net_amount="100.00",
            commission_rate="15",
        )
        base = convert_sale_financials_to_base(
            financials,
            conversion_rate="41.2345",
        )
        self.assertEqual(base.gross_amount, Decimal("4123.45"))
        self.assertEqual(base.commission_amount, Decimal("618.52"))
        self.assertEqual(base.partner_amount, Decimal("3504.93"))
        self.assertEqual(base.gross_amount, base.commission_amount + base.partner_amount)

    def test_own_sale_has_no_new_partner_debt(self) -> None:
        result = calculate_sale_financials(
            relationship_model="OWN",
            qty="2",
            net_amount="200",
        )

        self.assertEqual(result.partner_amount, Decimal("0"))
        self.assertEqual(result.retained_amount, Decimal("200.00"))

    def test_commission_uses_net_amount_and_preserves_rounding_identity(self) -> None:
        result = calculate_sale_financials(
            relationship_model="COMMISSION",
            qty="3",
            net_amount="100.01",
            commission_rate="15",
        )

        self.assertEqual(result.commission_amount, Decimal("15.00"))
        self.assertEqual(result.partner_amount, Decimal("85.01"))
        self.assertEqual(result.retained_amount, Decimal("15.00"))
        self.assertEqual(result.commission_amount + result.partner_amount, result.gross_amount)

    def test_consignment_uses_effective_partner_unit_rate(self) -> None:
        result = calculate_sale_financials(
            relationship_model="CONSIGNMENT",
            qty="2",
            net_amount="200",
            partner_unit_rate="70",
        )

        self.assertEqual(result.partner_amount, Decimal("140.00"))
        self.assertEqual(result.retained_amount, Decimal("60.00"))

    def test_negative_consignment_margin_requires_explicit_approval(self) -> None:
        with self.assertRaisesRegex(SaleFinancialError, "loss approval"):
            calculate_sale_financials(
                relationship_model="CONSIGNMENT",
                qty="2",
                net_amount="100",
                partner_unit_rate="60",
            )
        approved = calculate_sale_financials(
            relationship_model="CONSIGNMENT",
            qty="2",
            net_amount="100",
            partner_unit_rate="60",
            allow_negative_margin=True,
        )
        self.assertEqual(approved.retained_amount, Decimal("-20.00"))

    def test_invalid_models_rates_and_non_finite_values_fail_closed(self) -> None:
        with self.assertRaises(SaleFinancialError):
            calculate_sale_financials(relationship_model="OTHER", qty=1, net_amount=1)
        with self.assertRaises(SaleFinancialError):
            calculate_sale_financials(
                relationship_model="COMMISSION",
                qty=1,
                net_amount=1,
                commission_rate=0,
            )
        with self.assertRaises(SaleFinancialError):
            calculate_sale_financials(
                relationship_model="OWN",
                qty=1,
                net_amount="NaN",
            )
