"""Transactional integration coverage for the Ukrainian receiving workflow.

This module is skipped by the lightweight source-tree test run and is executed
inside a configured Frappe test site.
"""

from __future__ import annotations

import unittest

try:
	import frappe
except ModuleNotFoundError:  # Lightweight unit-test environment.
	frappe = None


@unittest.skipUnless(frappe, "requires a configured Frappe site")
class TestReceivingFrappe(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.savepoint("ua_receiving_smoke")
		self.company = self._get_company()
		self.supplier = self._ensure_supplier()
		self.item_code = self._ensure_item()
		self.warehouse = self._ensure_warehouse()

	def tearDown(self):
		frappe.db.rollback(save_point="ua_receiving_smoke")

	def _get_company(self):
		companies = frappe.get_all(
			"Company",
			filters={"default_currency": ("is", "set"), "cost_center": ("is", "set")},
			pluck="name",
			order_by="creation asc",
		)
		company = next((name for name in companies if not name.startswith("_")), None)
		self.assertTrue(company, "A configured test Company is required")
		return company

	def _ensure_supplier(self):
		name = "UA Receiving Integration Test Supplier"
		if frappe.db.exists("Supplier", name):
			return name
		supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
		return frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": name,
				"supplier_group": supplier_group,
				"supplier_type": "Company",
				"default_currency": frappe.get_cached_value(
					"Company", self.company, "default_currency"
				),
			}
		).insert(ignore_permissions=True).name

	def _ensure_item(self):
		item_code = "UA-RECEIVING-INTEGRATION-ITEM"
		if frappe.db.exists("Item", item_code):
			return item_code
		item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
		return frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": item_code,
				"item_name": "UA Receiving Integration Item",
				"item_group": item_group,
				"stock_uom": "Nos",
				"is_stock_item": 1,
			}
		).insert(ignore_permissions=True).name

	def _ensure_warehouse(self):
		abbr = frappe.get_cached_value("Company", self.company, "abbr")
		name = f"UA Receiving Integration - {abbr}"
		if frappe.db.exists("Warehouse", name):
			return name
		return frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": "UA Receiving Integration",
				"company": self.company,
			}
		).insert(ignore_permissions=True).name

	def test_receipt_posts_stock_then_creates_prices_labels_and_draft_invoice(self):
		from erpnext_ua.ua_receiving.service import (
			complete_receipt,
			preview_receipt_completion,
		)

		company = self.company
		supplier = self.supplier
		item_code = self.item_code
		warehouse = self.warehouse
		price_list = "Standard Selling"
		uom = frappe.get_cached_value("Item", item_code, "stock_uom")
		cost_center = frappe.get_cached_value("Company", company, "cost_center")
		before_qty = frappe.db.get_value(
			"Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty"
		) or 0

		receipt = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"company": company,
				"supplier": supplier,
				"currency": "UAH",
				"supplier_delivery_note": "UA-SMOKE-TRANSACTIONAL",
				"ua_supplier_document_type": "Видаткова накладна постачальника",
				"ua_supplier_document_date": frappe.utils.today(),
				"ua_supplier_document_file": "/private/files/ua-receiving-smoke.pdf",
				"ua_received_by": "Administrator",
				"ua_receipt_verified": 1,
				"items": [
					{
						"item_code": item_code,
						"qty": 2,
						"rate": 100,
						"warehouse": warehouse,
						"uom": uom,
						"stock_uom": uom,
						"conversion_factor": 1,
						"cost_center": cost_center,
					}
				],
			}
		)
		receipt.insert()
		receipt.submit()

		after_qty = frappe.db.get_value(
			"Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty"
		) or 0
		self.assertAlmostEqual(float(after_qty) - float(before_qty), 2)
		self.assertEqual(
			frappe.db.count(
				"GL Entry",
				{
					"voucher_type": "Purchase Receipt",
					"voucher_no": receipt.name,
					"party_type": "Supplier",
					"is_cancelled": 0,
				},
			),
			0,
		)
		if frappe.get_cached_value("Company", company, "enable_perpetual_inventory"):
			self.assertGreaterEqual(
				frappe.db.count(
					"GL Entry",
					{
						"voucher_type": "Purchase Receipt",
						"voucher_no": receipt.name,
						"is_cancelled": 0,
					},
				),
				2,
			)

		preview = preview_receipt_completion(receipt.name, price_list)
		self.assertEqual(preview["rows"][0]["warehouse"], warehouse)
		result = complete_receipt(
			receipt.name,
			[
				{
					"selected": 1,
					"source_row": preview["rows"][0]["source_row"],
					"new_price": 157,
					"copies": 2,
				}
			],
			price_list,
			create_purchase_invoice=1,
		)

		invoice = frappe.get_doc("Purchase Invoice", result["purchase_invoice"])
		self.assertEqual(invoice.docstatus, 0)
		self.assertEqual(invoice.ua_source_purchase_receipt, receipt.name)
		self.assertEqual(invoice.bill_no, receipt.supplier_delivery_note)
		invoice_gl_entries = frappe.get_all(
			"GL Entry",
			filters={
				"voucher_type": "Purchase Invoice",
				"voucher_no": invoice.name,
				"is_cancelled": 0,
			},
			fields=["name", "account", "party_type", "party", "debit", "credit", "is_cancelled"],
		)
		self.assertEqual(invoice_gl_entries, [], invoice_gl_entries)

		self.assertTrue(result["price_tag_jobs"])
		self.assertEqual(result["price_tag_prints"][0]["name"], result["price_tag_jobs"][0])
		self.assertTrue(result["price_tag_prints"][0]["print_format"])
		job = frappe.get_doc("Price Tag Print Job", result["price_tag_jobs"][0])
		self.assertEqual(job.warehouse, warehouse)
		self.assertEqual(job.items[0].source_warehouse, warehouse)
		self.assertEqual(job.items[0].selling_price, 157)
		self.assertEqual(job.items[0].copies, 2)
		self.assertEqual(
			frappe.db.get_value("Purchase Receipt", receipt.name, "ua_receiving_completed"),
			1,
		)

	def test_vat_checkbox_posts_only_the_gross_item_price(self):
		from erpnext_ua.ua_receiving.service import _create_purchase_invoice_draft

		company = self.company
		supplier = self.supplier
		item_code = self.item_code
		warehouse = self.warehouse
		uom = frappe.get_cached_value("Item", item_code, "stock_uom")
		cost_center = frappe.get_cached_value("Company", company, "cost_center")

		receipt = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"company": company,
				"supplier": supplier,
				"currency": "UAH",
				"supplier_delivery_note": "UA-SMOKE-VAT-IN-PRICE",
				"ua_supplier_document_type": "Видаткова накладна постачальника",
				"ua_supplier_document_date": frappe.utils.today(),
				"ua_supplier_document_file": "/private/files/ua-receiving-vat-smoke.pdf",
				"ua_received_by": "Administrator",
				"ua_receipt_verified": 1,
				"ua_add_vat_20_to_prices": 1,
				"items": [
					{
						"item_code": item_code,
						"qty": 2,
						"ua_price_without_vat": 100,
						"rate": 100,
						"warehouse": warehouse,
						"uom": uom,
						"stock_uom": uom,
						"conversion_factor": 1,
						"cost_center": cost_center,
					}
				],
			}
		)
		receipt.insert()
		self.assertEqual(receipt.items[0].rate, 120)
		self.assertEqual(receipt.grand_total, 240)
		self.assertEqual(receipt.taxes, [])
		control_sheet = frappe.get_print(
			"Purchase Receipt",
			receipt.name,
			"Прибуткова накладна (UA)",
		)
		self.assertIn("КОНТРОЛЬНИЙ ЛИСТ — ЧЕРНЕТКА", control_sheet)
		self.assertIn("120.00", control_sheet)
		receipt.submit()

		invoice_name = _create_purchase_invoice_draft(receipt)
		invoice = frappe.get_doc("Purchase Invoice", invoice_name)
		self.assertEqual(invoice.ua_add_vat_20_to_prices, 1)
		self.assertEqual(invoice.items[0].ua_price_without_vat, 100)
		self.assertEqual(invoice.items[0].rate, 120)
		self.assertEqual(invoice.grand_total, 240)
		self.assertEqual(invoice.taxes, [])
		invoice.submit()

		payable_credit = sum(
			float(row.credit)
			for row in frappe.get_all(
				"GL Entry",
				filters={
					"voucher_type": "Purchase Invoice",
					"voucher_no": invoice.name,
					"party_type": "Supplier",
					"is_cancelled": 0,
				},
				fields=["credit"],
			)
		)
		self.assertEqual(payable_credit, 240)


if __name__ == "__main__":
	unittest.main()
