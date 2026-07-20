from __future__ import annotations

import json
import unittest
from pathlib import Path

import erpnext_ua.hooks as hooks


APP = Path(__file__).resolve().parents[1]


class TestPriceTagContracts(unittest.TestCase):
	def test_module_and_source_buttons_are_registered(self):
		self.assertIn("UA Price Tags", (APP / "modules.txt").read_text(encoding="utf-8"))
		for doctype in ("Purchase Receipt", "Stock Entry", "Delivery Note", "Item"):
			self.assertEqual(hooks.doctype_js[doctype], "ua_price_tags/public/js/price_tag_source.js")
		self.assertIn("erpnext_ua.install.ensure_price_tag_setup", hooks.after_install)
		self.assertIn("erpnext_ua.install.ensure_price_tag_setup", hooks.after_migrate)

	def test_print_job_contains_immutable_snapshot_fields(self):
		path = APP / "ua_price_tags" / "doctype" / "price_tag_print_job" / "price_tag_print_job.json"
		doctype = json.loads(path.read_text(encoding="utf-8"))
		fields = {field["fieldname"]: field for field in doctype["fields"]}
		self.assertEqual(fields["items"]["options"], "Price Tag Print Job Item")
		self.assertIn("Packaging", fields["template_type"]["options"])
		self.assertEqual(fields["label_size"]["default"], "40×25 mm")
		self.assertTrue(fields["snapshot_hash"]["read_only"])
		self.assertTrue(fields["printed_at"]["read_only"])

		controller = path.with_suffix(".py").read_text(encoding="utf-8")
		self.assertIn("hashlib.sha256", controller)
		self.assertIn("Зафіксований пакет друку не можна змінювати", controller)

		service = (APP / "ua_price_tags" / "service.py").read_text(encoding="utf-8")
		self.assertIn("ignore_party=False", service)
		self.assertIn('PACKAGING_TEMPLATE_MODE = "Packaging Label"', service)
		self.assertIn("config.packaging_print_format", service)

		item_path = APP / "ua_price_tags" / "doctype" / "price_tag_print_job_item" / "price_tag_print_job_item.json"
		item_doctype = json.loads(item_path.read_text(encoding="utf-8"))
		item_fields = {field["fieldname"]: field for field in item_doctype["fields"]}
		self.assertFalse(item_fields["selling_price"].get("reqd", 0))

	def test_bundled_formats_target_print_job_and_40x25(self):
		formats = APP / "ua_price_tags" / "print_format"
		folders = (
			"price_tag_standard_40x25",
			"price_tag_promotional_40x25",
			"packaging_label_40x25",
		)
		for folder in folders:
			data = json.loads(next((formats / folder).glob("*.json")).read_text(encoding="utf-8"))
			self.assertEqual(data["doc_type"], "Price Tag Print Job")
			self.assertIn("40mm 25mm", data["html"])
			self.assertIn("row.copies", data["html"])

		packaging = json.loads(
			(formats / "packaging_label_40x25" / "packaging_label_40x25.json").read_text(
				encoding="utf-8"
			)
		)
		self.assertNotIn("selling_price", packaging["html"])

		settings_path = (
			APP / "ua_price_tags" / "doctype" / "price_tag_settings" / "price_tag_settings.json"
		)
		settings = json.loads(settings_path.read_text(encoding="utf-8"))
		settings_fields = {field["fieldname"]: field for field in settings["fields"]}
		self.assertEqual(
			settings_fields["packaging_print_format"]["default"],
			"Етикетка на упаковку 40x25",
		)


if __name__ == "__main__":
	unittest.main()
