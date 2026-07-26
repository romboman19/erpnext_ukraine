from datetime import date
from decimal import Decimal
from unittest import TestCase

from erpnext_consignment_and_commission.consignment_and_commission.services.accounting import (
    AccountingPlanError,
    build_commission_recognition,
    build_consignment_recognition,
    build_settlement_debt,
    calculate_currency_outstanding,
    calculate_exchange_difference,
    calculate_payment_base_amount,
    resolve_adjustment_posting_date,
    validate_payment_report_binding,
)


class AccountingPlanTests(TestCase):
    def test_commission_recognition_is_balanced(self) -> None:
        plan = build_commission_recognition("10000", "8500")

        self.assertEqual(plan.total_debit, Decimal("10000"))
        self.assertEqual(plan.total_credit, Decimal("10000"))
        self.assertEqual(
            [(line.account_key, line.debit, line.credit) for line in plan.lines],
            [
                ("principal_proceeds_deduction", Decimal("8500"), Decimal("0")),
                ("gross_sales_reclassification", Decimal("1500"), Decimal("0")),
                ("agency_service_revenue", Decimal("0"), Decimal("1500")),
                ("unreported_commission_liability", Decimal("0"), Decimal("8500")),
            ],
        )

    def test_negative_commission_margin_needs_explicit_approval(self) -> None:
        with self.assertRaises(AccountingPlanError):
            build_commission_recognition("5000", "8000")

        approved = build_commission_recognition("5000", "8000", allow_negative_margin=True)
        self.assertEqual(approved.total_debit, Decimal("8000"))

    def test_consignment_without_title_transfer_uses_net_revenue(self) -> None:
        plan = build_consignment_recognition("10000", "8000")

        self.assertEqual(plan.total_debit, Decimal("10000"))
        self.assertEqual(plan.total_credit, Decimal("10000"))
        self.assertNotIn("consignment_cogs", {line.account_key for line in plan.lines})
        self.assertEqual(
            [(line.account_key, line.debit, line.credit) for line in plan.lines],
            [
                ("principal_proceeds_deduction", Decimal("8000"), Decimal("0")),
                ("gross_sales_reclassification", Decimal("2000"), Decimal("0")),
                ("agency_service_revenue", Decimal("0"), Decimal("2000")),
                ("unreported_consignment_liability", Decimal("0"), Decimal("8000")),
            ],
        )

    def test_debt_plan_has_supplier_and_report_reference(self) -> None:
        plan = build_settlement_debt(
            "8500",
            relationship_model="COMMISSION",
            supplier="SUP-001",
            report_doctype="Third Party Settlement Report",
            report_name="TPR-001",
        )

        payable = plan.lines[-1]
        self.assertEqual(payable.party_type, "Supplier")
        self.assertEqual(payable.party, "SUP-001")
        self.assertEqual(payable.reference_name, "TPR-001")

    def test_payment_entry_is_bound_to_one_report(self) -> None:
        self.assertEqual(validate_payment_report_binding("TPR-001", ["TPR-001"]), "TPR-001")

        with self.assertRaises(AccountingPlanError):
            validate_payment_report_binding("TPR-001", ["TPR-001", "TPR-002"])

    def test_currency_outstanding_tracks_partial_payments(self) -> None:
        self.assertEqual(calculate_currency_outstanding("200", payments="100"), Decimal("100"))
        self.assertEqual(calculate_currency_outstanding("200", payments="200"), Decimal("0"))

    def test_payment_date_rate_drives_base_amount_and_exchange_difference(self) -> None:
        self.assertEqual(calculate_payment_base_amount("100", "41.20"), Decimal("4120.00"))
        self.assertEqual(calculate_exchange_difference("100", "40", "41.20"), Decimal("120.00"))

    def test_backdated_event_posts_on_first_open_date(self) -> None:
        economic_date = date(2026, 6, 15)

        self.assertEqual(
            resolve_adjustment_posting_date(economic_date, date(2026, 6, 30)),
            date(2026, 7, 1),
        )
        self.assertEqual(resolve_adjustment_posting_date(economic_date, None), economic_date)
