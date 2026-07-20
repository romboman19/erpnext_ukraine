from __future__ import annotations

from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_ua.ua_accounting.chart_setup import apply_chart
from erpnext_ua.ua_accounting.off_balance import create_off_balance_entry
from erpnext_ua.install import ensure_accounting_setup


class TestUAOffBalanceEntry(IntegrationTestCase):
	def setUp(self):
		ensure_accounting_setup()
		suffix = uuid4().hex[:5].upper()
		self.company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": f"_UA Off Balance Test {suffix}",
				"abbr": f"O{suffix[:4]}",
				"country": "Ukraine",
				"default_currency": "UAH",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "Standard",
			}
		).insert(ignore_permissions=True)
		apply_chart(self.company.name, "full_291", self.company.name)
		self.account_024 = frappe.db.get_value(
			"Account", {"company": self.company.name, "account_number": "024"}, "name"
		)

	def test_idempotent_entry_and_cancelled_audit(self):
		payload = {
			"company": self.company.name,
			"posting_date": "2026-07-20",
			"off_balance_account": self.account_024,
			"direction": "Increase",
			"quantity": 2,
			"uom": "Nos",
			"amount": 1500,
			"currency": "UAH",
			"external_reference_key": f"test-{uuid4().hex}",
		}
		first = create_off_balance_entry(payload, ignore_permissions=True)
		retry = create_off_balance_entry(payload, ignore_permissions=True)
		self.assertEqual(first.name, retry.name)
		self.assertEqual(first.docstatus, 1)

		decrease = create_off_balance_entry(
			{
				**payload,
				"direction": "Decrease",
				"quantity": 1,
				"amount": 750,
				"external_reference_key": f"test-decrease-{uuid4().hex}",
			},
			ignore_permissions=True,
		)
		with self.assertRaises(frappe.ValidationError):
			first.cancel()

		decrease.cancel()
		first.cancel()
		first.reload()
		self.assertEqual(first.docstatus, 2)
		with self.assertRaises(frappe.ValidationError):
			create_off_balance_entry(payload, ignore_permissions=True)

	def test_regular_gl_account_is_rejected(self):
		regular_account = frappe.db.get_value(
			"Account", {"company": self.company.name, "account_number": "281"}, "name"
		)
		doc = frappe.get_doc(
			{
				"doctype": "UA Off Balance Entry",
				"company": self.company.name,
				"posting_date": "2026-07-20",
				"off_balance_account": regular_account,
				"direction": "Increase",
				"quantity": 1,
				"uom": "Nos",
				"currency": "UAH",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)
