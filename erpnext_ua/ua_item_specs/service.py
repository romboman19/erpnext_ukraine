"""Category-tree resolution and caching for item specifications."""

from __future__ import annotations

import frappe

from erpnext_ua.ua_item_specs.domain import merge_group_specifications


CACHE_PREFIX = "ua_item_specs::group::"
SPEC_FIELDS = (
	"name",
	"spec_name",
	"spec_group",
	"field_type",
	"unit",
	"spec_precision",
	"min_value",
	"max_value",
	"is_active",
	"allow_in_filters",
)


def clear_specification_cache(doc=None, method=None):
	"""Drop every cached category set.

	Invalidating only the edited branch would be cheaper, but moving a nested-set node
	changes inheritance on both sides of the move, and a stale set silently produces wrong
	mandatory fields. Category and specification edits are rare, so clearing the whole
	prefix is the safer trade.
	"""
	frappe.cache().delete_keys(CACHE_PREFIX)


def get_group_specifications(item_group: str | None) -> list[dict]:
	"""Effective specification set of a category, inherited ancestors included."""
	if not item_group:
		return []
	cache_key = f"{CACHE_PREFIX}{item_group}"
	cached = frappe.cache().get_value(cache_key)
	if cached is not None:
		return cached
	resolved = _resolve(item_group)
	frappe.cache().set_value(cache_key, resolved)
	return resolved


def load_specifications(names) -> dict[str, dict]:
	"""Master data plus options for the given specifications, keyed by name.

	Used by validation, which must also cover rows added by hand and therefore absent
	from the category set.
	"""
	names = [name for name in dict.fromkeys(names) if name]
	if not names:
		return {}
	masters = frappe.get_all(
		"UA Item Specification", filters={"name": ("in", names)}, fields=list(SPEC_FIELDS)
	)
	options = _options_for([master.name for master in masters])
	return {master.name: {**master, "options": options.get(master.name, [])} for master in masters}


def _resolve(item_group: str) -> list[dict]:
	levels = [
		(
			group,
			frappe.get_all(
				"UA Item Group Specification",
				filters={"parent": group, "parenttype": "Item Group"},
				fields=["specification", "is_mandatory", "default_value"],
				order_by="idx",
			),
		)
		for group in _lineage(item_group)
	]
	return _decorate(merge_group_specifications(levels))


def _lineage(item_group: str) -> list[str]:
	"""Ancestors root-first, then the category itself — the order the merge expects."""
	from frappe.utils.nestedset import get_ancestors_of

	ancestors = get_ancestors_of("Item Group", item_group, order_by="lft asc") or []
	return [*ancestors, item_group]


def _decorate(rows: list[dict]) -> list[dict]:
	names = [row["specification"] for row in rows]
	if not names:
		return []
	masters = load_specifications(names)
	decorated = []
	for row in rows:
		master = masters.get(row["specification"])
		if not master:
			# The specification was deleted; the set simply stops offering it.
			continue
		decorated.append(
			{
				**row,
				"spec_label": master.get("spec_name"),
				"spec_group": master.get("spec_group"),
				"field_type": master.get("field_type"),
				"unit": master.get("unit"),
				"spec_precision": master.get("spec_precision"),
				"min_value": master.get("min_value"),
				"max_value": master.get("max_value"),
				"is_active": int(master.get("is_active") or 0),
				"options": master.get("options") or [],
			}
		)
	return decorated


def _options_for(names) -> dict[str, list[dict]]:
	if not names:
		return {}
	rows = frappe.get_all(
		"UA Item Specification Option",
		filters={"parent": ("in", names), "parenttype": "UA Item Specification"},
		fields=["parent", "option_value", "option_label", "is_active"],
		order_by="parent asc, idx asc",
	)
	grouped: dict[str, list[dict]] = {}
	for row in rows:
		grouped.setdefault(row.parent, []).append(
			{
				"value": row.option_value,
				"label": row.option_label or row.option_value,
				"is_active": int(row.is_active or 0),
			}
		)
	return grouped
