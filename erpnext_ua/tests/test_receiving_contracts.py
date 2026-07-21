from __future__ import annotations

import json
import unittest
from pathlib import Path

import erpnext_ua.hooks as hooks


APP = Path(__file__).resolve().parents[1]


class TestReceivingContracts(unittest.TestCase):
	def test_receipt_validation_and_upgrade_hooks_are_registered(self):
		self.assertEqual(
			hooks.doc_events["Purchase Receipt"]["before_submit"],
			"erpnext_ua.ua_receiving.service.validate_purchase_receipt",
		)
		self.assertEqual(
			hooks.doc_events["Purchase Receipt"]["before_validate"],
			"erpnext_ua.ua_receiving.pricing.apply_supplier_price_vat",
		)
		self.assertEqual(
			hooks.doc_events["Purchase Invoice"]["before_validate"],
			"erpnext_ua.ua_receiving.pricing.apply_supplier_price_vat",
		)
		for lifecycle in (hooks.after_install, hooks.after_migrate):
			self.assertIn("erpnext_ua.install.ensure_receiving_setup", lifecycle)
			repair = "erpnext_ua.install.ensure_price_tag_doctypes"
			setup = "erpnext_ua.install.ensure_price_tag_setup"
			self.assertLess(lifecycle.index(repair), lifecycle.index(setup))

	def test_receiving_fields_and_safe_draft_invoice_are_present(self):
		install = (APP / "install.py").read_text(encoding="utf-8")
		for fieldname in (
			"ua_supplier_document_type",
			"ua_supplier_document_date",
			"ua_supplier_document_file",
			"ua_received_by",
			"ua_receipt_verified",
			"ua_purchase_invoice",
			"ua_add_vat_20_to_prices",
			"ua_price_without_vat",
		):
			self.assertIn(f'"fieldname": "{fieldname}"', install)

		service = (APP / "ua_receiving" / "service.py").read_text(encoding="utf-8")
		self.assertIn("make_purchase_invoice(receipt.name)", service)
		self.assertIn("invoice.insert()", service)
		self.assertNotIn("invoice.submit()", service)
		self.assertIn("invoice.ua_add_vat_20_to_prices", service)

	def test_receipt_completion_requires_submit_and_repairs_warehouse(self):
		javascript = (APP / "public" / "js" / "price_tag_source.js").read_text(
			encoding="utf-8"
		)
		self.assertIn('doctype === "Purchase Receipt" && frm.doc.docstatus !== 1', javascript)
		self.assertIn('__("Завершити приймання")', javascript)

		service = (APP / "ua_price_tags" / "service.py").read_text(encoding="utf-8")
		self.assertIn('source_doctype == "Purchase Receipt" and doc.docstatus != 1', service)
		self.assertIn("resolve_receipt_warehouse(", service)

	def test_a4_control_sheet_is_available_for_draft_receipts(self):
		path = (
			APP
			/ "ua_receiving"
			/ "print_format"
			/ "prybutkova_nakladna_ua"
			/ "prybutkova_nakladna_ua.json"
		)
		data = json.loads(path.read_text(encoding="utf-8"))
		self.assertEqual(data["doc_type"], "Purchase Receipt")
		self.assertEqual(data["name"], "Прибуткова накладна (UA)")
		self.assertIn("КОНТРОЛЬНИЙ ЛИСТ — ЧЕРНЕТКА", data["html"])
		self.assertIn("Закуп. ціна", data["html"])
		self.assertIn("Перевірив", data["html"])

		javascript = (APP / "public" / "js" / "price_tag_source.js").read_text(encoding="utf-8")
		self.assertIn('__("Контрольний лист A4")', javascript)


if __name__ == "__main__":
	unittest.main()
