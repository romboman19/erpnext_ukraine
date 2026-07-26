"""Wiring contracts for the item specification module.

These run without a bench: they read the shipped JSON and source so that a missing hook or
a type without a storage column fails in CI instead of on a site.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import erpnext_ua.hooks as hooks
from erpnext_ua.ua_item_specs.domain import FIELD_TYPES, VALUE_FIELD_BY_TYPE


APP = Path(__file__).resolve().parents[1]
MODULE = APP / "ua_item_specs"


def doctype_json(folder: str) -> dict:
	return json.loads((MODULE / "doctype" / folder / f"{folder}.json").read_text(encoding="utf-8"))


class TestItemSpecificationWiring(unittest.TestCase):
	def test_module_is_registered_for_the_app(self):
		modules = (APP / "modules.txt").read_text(encoding="utf-8").split("\n")
		self.assertIn("UA Item Specs", modules)

	def test_item_and_item_group_events_are_hooked(self):
		self.assertEqual(
			hooks.doc_events["Item"]["validate"],
			"erpnext_ua.ua_item_specs.item_hooks.validate_specifications",
		)
		self.assertEqual(
			hooks.doc_events["Item Group"]["validate"],
			"erpnext_ua.ua_item_specs.item_hooks.validate_group_specifications",
		)
		self.assertEqual(
			hooks.doc_events["Item Group"]["on_update"],
			"erpnext_ua.ua_item_specs.item_hooks.clear_specification_cache",
		)

	def test_form_script_is_published_without_dropping_the_existing_one(self):
		scripts = hooks.doctype_js["Item"]
		self.assertIn("public/js/item_specifications.js", scripts)
		self.assertIn("public/js/price_tag_source.js", scripts)

	def test_doctypes_are_imported_before_the_fields_that_reference_them(self):
		for stage in (hooks.after_install, hooks.after_migrate):
			self.assertLess(
				stage.index("erpnext_ua.install.ensure_item_spec_doctypes"),
				stage.index("erpnext_ua.install.ensure_item_spec_setup"),
			)

	def test_tables_are_attached_to_item_and_item_group(self):
		install = (APP / "install.py").read_text(encoding="utf-8")
		self.assertIn('"options": "UA Item Group Specification"', install)
		self.assertIn('"options": "UA Item Specification Value"', install)
		self.assertIn('ITEM_SPEC_ROLES = ("Specification Manager",)', install)


class TestSpecificationSchema(unittest.TestCase):
	def test_every_declared_type_has_a_storage_column(self):
		"""The Select options and the typed columns must not drift apart."""
		spec = doctype_json("ua_item_specification")
		declared = next(f for f in spec["fields"] if f["fieldname"] == "field_type")["options"].split("\n")
		self.assertEqual(sorted(declared), sorted(FIELD_TYPES))

		value_fields = {f["fieldname"] for f in doctype_json("ua_item_specification_value")["fields"]}
		for field_type in declared:
			self.assertIn(VALUE_FIELD_BY_TYPE[field_type], value_fields)

	def test_child_tables_are_marked_as_tables(self):
		for folder in (
			"ua_item_specification_option",
			"ua_item_group_specification",
			"ua_item_specification_value",
		):
			self.assertEqual(doctype_json(folder).get("istable"), 1, folder)

	def test_value_grid_shows_only_the_specification_and_its_rendered_value(self):
		# Nine typed columns in the grid would be unreadable; the rest live in the
		# expanded row and are picked by depends_on.
		fields = doctype_json("ua_item_specification_value")["fields"]
		in_list = [f["fieldname"] for f in fields if f.get("in_list_view")]
		self.assertEqual(in_list, ["specification", "display_value"])

	def test_stored_value_and_display_value_are_indexed_for_search(self):
		fields = doctype_json("ua_item_specification_value")["fields"]
		indexed = {f["fieldname"] for f in fields if f.get("search_index")}
		self.assertEqual(indexed, {"specification", "display_value"})

	def test_display_value_is_never_user_editable(self):
		fields = {f["fieldname"]: f for f in doctype_json("ua_item_specification_value")["fields"]}
		self.assertEqual(fields["display_value"].get("read_only"), 1)
		self.assertEqual(fields["field_type"].get("read_only"), 1)
		self.assertEqual(fields["source"].get("read_only"), 1)

	def test_precision_field_avoids_the_reserved_document_attribute(self):
		# `Document.precision()` is a method; a field literally named `precision` shadows
		# it and breaks Frappe's own float rounding for the DocType.
		fields = {f["fieldname"] for f in doctype_json("ua_item_specification")["fields"]}
		self.assertIn("spec_precision", fields)
		self.assertNotIn("precision", fields)

	def test_report_belongs_to_the_module_and_filters_numerically(self):
		report = json.loads(
			(MODULE / "report" / "item_specifications" / "item_specifications.json").read_text(
				encoding="utf-8"
			)
		)
		self.assertEqual(report["module"], "UA Item Specs")
		self.assertEqual(report["report_type"], "Script Report")
		filters = {row["fieldname"]: row for row in report["filters"]}
		self.assertEqual(filters["number_from"]["fieldtype"], "Float")
		self.assertEqual(filters["number_to"]["fieldtype"], "Float")

		source = (MODULE / "report" / "item_specifications" / "item_specifications.py").read_text(
			encoding="utf-8"
		)
		# Numeric filtering has to hit the typed columns, not the rendered text.
		self.assertIn("value.value_float", source)
		self.assertIn("value.value_int", source)
		self.assertIn("item_group.lft >= %(lft)s", source)


class TestServerSideEnforcement(unittest.TestCase):
	def test_validation_lives_on_the_server_not_only_in_the_form_script(self):
		hooks_source = (MODULE / "item_hooks.py").read_text(encoding="utf-8")
		for guard in (
			"_reject_duplicates",
			"_require_mandatory",
			"_validate_values",
			"_write_display_values",
			"_fill_metadata",
		):
			self.assertIn(guard, hooks_source)

	def test_mandatory_values_are_reported_as_one_list(self):
		hooks_source = (MODULE / "item_hooks.py").read_text(encoding="utf-8")
		self.assertIn('Заповніть обов\'язкові характеристики: {0}").format(", ".join(missing))', hooks_source)

	def test_hooks_stay_inert_until_the_custom_field_exists(self):
		# doc_events go live with the new hooks.py, but the Table field is created later in
		# after_migrate; documents saved in between must not blow up.
		hooks_source = (MODULE / "item_hooks.py").read_text(encoding="utf-8")
		self.assertIn("def _table_missing(doc)", hooks_source)
		self.assertEqual(hooks_source.count("if _table_missing(doc):"), 2)

	def test_specification_master_guards_slug_and_type_changes(self):
		controller = (
			MODULE / "doctype" / "ua_item_specification" / "ua_item_specification.py"
		).read_text(encoding="utf-8")
		self.assertIn("_validate_fieldname", controller)
		self.assertIn("_guard_type_change", controller)
		self.assertIn("def on_trash", controller)
		self.assertIn("_used_in_items", controller)


if __name__ == "__main__":
	unittest.main()
