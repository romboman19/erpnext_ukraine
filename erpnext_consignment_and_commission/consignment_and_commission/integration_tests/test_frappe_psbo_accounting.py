"""Ukrainian chart-to-commission mapping acceptance."""

from __future__ import annotations

from uuid import uuid4

import frappe
from erpnext_ua.ua_accounting.chart_setup import apply_chart
from frappe.tests import IntegrationTestCase

from ..setup.psbo_accounting import configure_psbo_account_mapping


class TestFrappePSBOAccounting(IntegrationTestCase):
    def _company_with_chart(self, template: str) -> frappe.model.document.Document:
        if not frappe.db.exists("Warehouse Type", "Transit"):
            frappe.get_doc(
                {"doctype": "Warehouse Type", "name": "Transit"}
            ).insert(ignore_permissions=True)
        suffix = uuid4().hex[:5].upper()
        company = frappe.get_doc(
            {
                "doctype": "Company",
                "company_name": f"_CC PSBO {template} {suffix}",
                "abbr": f"C{suffix[:4]}",
                "country": "Ukraine",
                "default_currency": "UAH",
                "create_chart_of_accounts_based_on": "Standard Template",
                "chart_of_accounts": "Standard",
            }
        ).insert(ignore_permissions=True)
        apply_chart(company.name, template, company.name)
        company.reload()
        return company

    def test_full_and_simplified_charts_resolve_production_mapping(self) -> None:
        expectations = {
            "full_291": {
                "off_balance_goods_account": "024",
                "gross_proceeds_clearing_account": "702",
                "commission_revenue_account": "703",
                "principal_proceeds_deduction_account": "704",
                "unreported_commission_liability_account": "685",
                "unreported_consignment_liability_account": "685",
            },
            "simplified_186": {
                "off_balance_goods_account": "024",
                "gross_proceeds_clearing_account": "70.1",
                "commission_revenue_account": "70.3",
                "principal_proceeds_deduction_account": "70.2",
                "unreported_commission_liability_account": "68.6",
                "unreported_consignment_liability_account": "68.7",
            },
        }
        for template, expected in expectations.items():
            with self.subTest(template=template):
                company = self._company_with_chart(template)
                mapping = configure_psbo_account_mapping(company.name)
                mapping.run_method("validate")
                for fieldname, account_number in expected.items():
                    account = frappe.get_doc("Account", mapping.get(fieldname))
                    self.assertEqual(account.account_number, account_number)
                    self.assertEqual(account.company, company.name)
                off_balance = frappe.get_doc(
                    "Account",
                    mapping.off_balance_goods_account,
                )
                self.assertTrue(off_balance.disabled)
                self.assertTrue(off_balance.ua_off_balance)
                self.assertEqual(
                    mapping.default_supplier_payable_account,
                    company.default_payable_account,
                )
                if template == "simplified_186":
                    for fieldname in (
                        "commission_revenue_account",
                        "unreported_commission_liability_account",
                        "unreported_consignment_liability_account",
                    ):
                        account = frappe.get_doc("Account", mapping.get(fieldname))
                        self.assertEqual(account.ua_legal_source, "erpnext_extension")
                        self.assertEqual(account.ua_chart_template, "simplified_186")
