from __future__ import annotations

import json
import unittest
from pathlib import Path

import erpnext_ua.hooks as hooks
from erpnext_ua.print_designer_documents import RECEIPT_FORMAT_NAME, build_document_formats
from erpnext_ua.print_designer_price_tags import (
	PRICE_TAG_FORMAT_FIELDS,
	build_price_tag_formats,
)


APP = Path(__file__).resolve().parents[1]


class TestPriceTagContracts(unittest.TestCase):
	def test_module_and_source_buttons_are_registered(self):
		self.assertIn("UA Price Tags", (APP / "modules.txt").read_text(encoding="utf-8"))
		for doctype in ("Stock Entry", "Delivery Note", "Item"):
			self.assertEqual(hooks.doctype_js[doctype], "public/js/price_tag_source.js")
		self.assertIn("public/js/price_tag_source.js", hooks.doctype_js["Purchase Receipt"])
		self.assertIn("erpnext_ua.install.ensure_price_tag_setup", hooks.after_install)
		self.assertIn("erpnext_ua.install.ensure_price_tag_setup", hooks.after_migrate)
		self.assertIn(
			"erpnext_ua.print_designer_setup.ensure_print_designer_formats", hooks.after_install
		)
		self.assertIn(
			"erpnext_ua.print_designer_setup.ensure_print_designer_formats", hooks.after_migrate
		)
		self.assertLess(
			hooks.after_install.index("erpnext_ua.install.ensure_price_tag_setup"),
			hooks.after_install.index(
				"erpnext_ua.print_designer_setup.ensure_print_designer_formats"
			),
		)

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
			{
				fieldname: settings_fields[fieldname]["default"]
				for fieldname in PRICE_TAG_FORMAT_FIELDS
			},
			{
				fieldname: legacy_name
				for fieldname, (legacy_name, _designer_name) in PRICE_TAG_FORMAT_FIELDS.items()
			},
		)

	def test_native_print_designer_formats_are_editable_and_renderable(self):
		base_settings = {"globalStyles": {}, "schema_version": "1.3.0"}
		price_formats = build_price_tag_formats(base_settings)
		self.assertEqual(
			{row["name"] for row in price_formats},
			{designer_name for _legacy_name, designer_name in PRICE_TAG_FORMAT_FIELDS.values()},
		)
		for format_doc in price_formats:
			self._assert_native_designer_format(format_doc, "Price Tag Print Job")
			settings = json.loads(format_doc["print_designer_settings"])
			self.assertAlmostEqual(settings["page"]["width"], 40 * 96 / 25.4)
			self.assertAlmostEqual(settings["page"]["height"], 25 * 96 / 25.4)
			self.assertIn("row.copies", settings["userProvidedJinja"])
			self.assertIn('doc.set("items", expanded_items)', settings["userProvidedJinja"])
			body = json.loads(format_doc["print_designer_body"])
			self.assertEqual(body[0]["childrens"][0]["type"], "table")

		document_formats = build_document_formats(base_settings)
		self.assertEqual(len(document_formats), 4)
		self.assertIn(RECEIPT_FORMAT_NAME, {row["name"] for row in document_formats})
		for format_doc in document_formats:
			self._assert_native_designer_format(format_doc, format_doc["doc_type"])
			body = json.loads(format_doc["print_designer_body"])
			self.assertIn("table", {element["type"] for element in body[0]["childrens"]})

	def _assert_native_designer_format(self, format_doc, expected_doctype):
		self.assertEqual(format_doc["doc_type"], expected_doctype)
		self.assertEqual(format_doc["print_designer"], 1)
		self.assertEqual(format_doc["standard"], "No")
		self.assertTrue(json.loads(format_doc["print_designer_body"]))
		layout = json.loads(format_doc["print_designer_print_format"])
		self.assertTrue(layout["body"][0]["childrens"])

	def test_created_jobs_open_the_print_view_directly(self):
		javascript = (APP / "public" / "js" / "price_tag_source.js").read_text(encoding="utf-8")
		list_javascript = (
			APP
			/ "ua_price_tags"
			/ "doctype"
			/ "price_tag_print_job"
			/ "price_tag_print_job_list.js"
		).read_text(encoding="utf-8")
		self.assertIn('window.location.assign(print_view_url("Price Tag Print Job"', javascript)
		self.assertIn('window.location.assign(`/printview?', list_javascript)


if __name__ == "__main__":
	unittest.main()
