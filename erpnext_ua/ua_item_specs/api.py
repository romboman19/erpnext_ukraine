"""Whitelisted endpoints for the item specification module."""

from __future__ import annotations

import frappe
from frappe import _

from erpnext_ua.ua_item_specs import service
from erpnext_ua.ua_item_specs.domain import VALUE_FIELDS
from erpnext_ua.ua_item_specs.item_hooks import SPEC_TABLE, template_rows


@frappe.whitelist()
def get_group_specifications(item_group: str) -> list[dict]:
	"""Effective set of a category, inheritance resolved, ready for the form script."""
	frappe.has_permission("Item Group", "read", throw=True)
	return service.get_group_specifications(item_group)


@frappe.whitelist()
def get_specification_options(specification: str) -> list[dict]:
	"""Active options of one specification, for the Select and MultiSelect controls."""
	frappe.has_permission("UA Item Specification", "read", throw=True)
	master = service.load_specifications([specification]).get(specification)
	if not master:
		return []
	return [
		{"value": option["value"], "label": option["label"]}
		for option in master.get("options") or []
		if option.get("is_active")
	]


@frappe.whitelist()
def sync_item_specifications(item: str) -> dict:
	"""Re-apply the category set to one item: adds what is missing, removes nothing.

	Meant for scripts and bulk fixes. The form button does the same thing client-side
	without saving, because a newly added mandatory row is empty by definition and saving
	would fail on it before the user gets a chance to fill it in.
	"""
	doc = frappe.get_doc("Item", item)
	doc.check_permission("write")
	before = {str(row.specification) for row in doc.get(SPEC_TABLE) or [] if row.specification}
	doc.save()
	after = {str(row.specification) for row in doc.get(SPEC_TABLE) or [] if row.specification}
	return {"item": doc.name, "added": sorted(after - before)}


@frappe.whitelist()
def copy_specifications_from_template(item: str) -> dict:
	"""Refresh a variant from its template: template values win, the variant's extra rows stay."""
	doc = frappe.get_doc("Item", item)
	doc.check_permission("write")
	if not doc.get("variant_of"):
		frappe.throw(_("Товар {0} не є варіантом шаблону").format(doc.name))

	pending = {str(row["specification"]): row for row in template_rows(doc.get("variant_of"))}
	updated = []
	for row in doc.get(SPEC_TABLE) or []:
		source = pending.pop(str(row.specification), None)
		if not source:
			continue
		for field in VALUE_FIELDS:
			row.set(field, source.get(field))
		updated.append(str(row.specification))
	for specification, source in pending.items():
		doc.append(SPEC_TABLE, source)
		updated.append(specification)

	doc.save()
	return {"item": doc.name, "updated": sorted(updated)}
