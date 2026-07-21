from __future__ import annotations

import unittest
from uuid import uuid4

try:
	import frappe
except ModuleNotFoundError:
	frappe = None
	IntegrationTestCase = unittest.TestCase
else:
	from frappe.tests import IntegrationTestCase

	from erpnext_ua.ua_accounting.chart_of_accounts import load_template
	from erpnext_ua.ua_accounting.chart_setup import apply_chart, preflight


@unittest.skipIf(frappe is None, "requires a Frappe test site")
class TestUAChartSetup(IntegrationTestCase):
	def _apply_and_assert(self, template_key: str):
		if not frappe.db.exists("Warehouse Type", "Transit"):
			frappe.get_doc(
				{
					"doctype": "Warehouse Type",
					"name": "Transit",
				}
			).insert(ignore_permissions=True)

		suffix = uuid4().hex[:5].upper()
		company_name = f"_UA Chart {template_key} Test {suffix}"
		company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": company_name,
				"abbr": f"U{suffix[:4]}",
				"country": "Ukraine",
				"default_currency": "UAH",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "Standard",
			}
		).insert(ignore_permissions=True)

		check = preflight(company.name, template_key)
		self.assertTrue(check["allowed"], check["blockers"])

		result = apply_chart(company.name, template_key, company.name)
		template = load_template(template_key)
		company.reload()

		self.assertEqual(result["template"]["account_count"], len(template["accounts"]))
		self.assertEqual(company.ua_chart_template, template_key)
		self.assertEqual(company.ua_chart_revision, "2025-12-23")
		self.assertGreaterEqual(result["created_account_count"], len(template["accounts"]))

		for fieldname, account_number in template["defaults"].items():
			account = frappe.db.get_value(
				"Account",
				{
					"company": company.name,
					"account_number": account_number,
					"is_group": 0,
				},
				"name",
			)
			self.assertTrue(account, f"Missing default account {account_number}")
			if company.meta.has_field(fieldname):
				self.assertEqual(company.get(fieldname), account)

		off_balance_accounts = frappe.get_all(
			"Account",
			filters={"company": company.name, "ua_off_balance": 1},
			fields=["disabled", "ua_chart_template", "ua_legal_source"],
		)
		self.assertTrue(off_balance_accounts)
		self.assertTrue(all(row.disabled for row in off_balance_accounts))
		self.assertTrue(all(row.ua_chart_template == template_key for row in off_balance_accounts))
		self.assertTrue(all(row.ua_legal_source == "official" for row in off_balance_accounts))

	def test_apply_simplified_chart_to_unused_ukrainian_company(self):
		self._apply_and_assert("simplified_186")

	def test_apply_full_chart_to_unused_ukrainian_company(self):
		self._apply_and_assert("full_291")
