"""Item specifications with the numeric filtering the typed columns exist for."""

from __future__ import annotations

import frappe
from frappe import _


# Only Int and Float carry a comparable number; every other type filters as text.
NUMBER_EXPRESSION = """case
	when value.field_type = 'Float' then value.value_float
	when value.field_type = 'Int' then value.value_int
end"""


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return _columns(), _rows(filters)


def _columns() -> list[dict]:
	return [
		{
			"fieldname": "item_code",
			"label": _("Товар"),
			"fieldtype": "Link",
			"options": "Item",
			"width": 180,
		},
		{"fieldname": "item_name", "label": _("Назва"), "fieldtype": "Data", "width": 220},
		{
			"fieldname": "item_group",
			"label": _("Категорія"),
			"fieldtype": "Link",
			"options": "Item Group",
			"width": 170,
		},
		{
			"fieldname": "specification",
			"label": _("Характеристика"),
			"fieldtype": "Link",
			"options": "UA Item Specification",
			"width": 180,
		},
		{"fieldname": "display_value", "label": _("Значення"), "fieldtype": "Data", "width": 200},
		{"fieldname": "number", "label": _("Число"), "fieldtype": "Float", "width": 100},
		{"fieldname": "unit", "label": _("Одиниця"), "fieldtype": "Data", "width": 90},
		{"fieldname": "field_type", "label": _("Тип"), "fieldtype": "Data", "width": 100},
		{"fieldname": "is_mandatory", "label": _("Обов'язкова"), "fieldtype": "Check", "width": 110},
	]


def _rows(filters) -> list[dict]:
	conditions = []
	values = {}

	if not filters.get("include_disabled"):
		conditions.append("item.disabled = 0")

	if filters.get("item_group"):
		# Report on "Ножі" should cover "Ножі → Складані" too, so filter by the subtree.
		bounds = frappe.db.get_value("Item Group", filters.get("item_group"), ["lft", "rgt"])
		if not bounds:
			return []
		values["lft"], values["rgt"] = bounds
		conditions.append("item_group.lft >= %(lft)s and item_group.rgt <= %(rgt)s")

	if filters.get("specification"):
		conditions.append("value.specification = %(specification)s")
		values["specification"] = filters.get("specification")

	if filters.get("number_from") is not None:
		conditions.append(f"({NUMBER_EXPRESSION}) >= %(number_from)s")
		values["number_from"] = filters.get("number_from")

	if filters.get("number_to") is not None:
		conditions.append(f"({NUMBER_EXPRESSION}) <= %(number_to)s")
		values["number_to"] = filters.get("number_to")

	if filters.get("value_contains"):
		conditions.append("value.display_value like %(value_contains)s")
		values["value_contains"] = f"%{filters.get('value_contains')}%"

	if filters.get("mandatory_only"):
		conditions.append("value.is_mandatory = 1")

	where = f"where {' and '.join(conditions)}" if conditions else ""

	return frappe.db.sql(
		f"""select
			value.parent as item_code,
			item.item_name as item_name,
			item.item_group as item_group,
			value.specification as specification,
			value.display_value as display_value,
			{NUMBER_EXPRESSION} as number,
			value.unit as unit,
			value.field_type as field_type,
			value.is_mandatory as is_mandatory
		from `tabUA Item Specification Value` value
		join `tabItem` item on item.name = value.parent
		join `tabItem Group` item_group on item_group.name = item.item_group
		{where}
		order by item.item_name asc, value.idx asc""",
		values,
		as_dict=True,
	)
