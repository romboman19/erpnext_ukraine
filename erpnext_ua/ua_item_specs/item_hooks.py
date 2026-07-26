"""Item and Item Group document events for the specification module.

Everything enforced here is enforced on the server on purpose: Data Import, the REST API,
patches and ``bench execute`` never run the form scripts.
"""

from __future__ import annotations

import frappe
from frappe import _

from erpnext_ua.ua_item_specs import domain
from erpnext_ua.ua_item_specs.service import (
	clear_specification_cache as _clear_cache,
	get_group_specifications,
	load_specifications,
)


SPEC_TABLE = "ua_specifications"


def clear_specification_cache(doc=None, method=None):
	"""The category tree changed, so every inherited set has to be recomputed."""
	_clear_cache()


def _table_missing(doc) -> bool:
	"""True until ``ensure_item_spec_setup`` has created the custom field.

	``doc_events`` are live as soon as the new hooks.py is deployed, but the Table field is
	created later in ``after_migrate``. A document saved inside that window must pass
	through untouched rather than fail on a field that does not exist yet.
	"""
	return not doc.meta.has_field(SPEC_TABLE)


def validate_group_specifications(doc, method=None):
	if _table_missing(doc):
		return
	rows = doc.get(SPEC_TABLE) or []
	names = [str(row.specification or "").strip() for row in rows if row.specification]
	repeated = domain.repeated_values(names)
	if repeated:
		frappe.throw(
			_("Характеристика повторюється в наборі категорії: {0}").format(", ".join(repeated))
		)


def validate_specifications(doc, method=None):
	if _table_missing(doc):
		return
	_seed_from_template(doc)
	category = {row["specification"]: row for row in get_group_specifications(doc.get("item_group"))}
	_append_missing_mandatory(doc, category)

	rows = doc.get(SPEC_TABLE) or []
	if not rows:
		return

	_reject_duplicates(rows)
	masters = load_specifications([row.specification for row in rows])
	_fill_metadata(rows, masters, category)
	_require_mandatory(rows)
	_validate_values(rows, masters)
	_write_display_values(rows, masters)


def _seed_from_template(doc):
	"""A new variant starts from its template's values — it is the same product in another size."""
	if not doc.get("variant_of") or doc.get(SPEC_TABLE) or not doc.is_new():
		return
	for row in template_rows(doc.get("variant_of")):
		doc.append(SPEC_TABLE, row)


def template_rows(template: str) -> list[dict]:
	return frappe.get_all(
		"UA Item Specification Value",
		filters={"parent": template, "parenttype": "Item"},
		fields=[
			"specification",
			"spec_label",
			"field_type",
			"unit",
			"is_mandatory",
			"source",
			*domain.VALUE_FIELDS,
		],
		order_by="idx asc",
	)


def _append_missing_mandatory(doc, category: dict):
	"""Pull in the category's mandatory rows. Optional ones are offered by the UI, not forced."""
	present = {str(row.specification) for row in doc.get(SPEC_TABLE) or [] if row.specification}
	for specification, spec in category.items():
		if specification in present or not spec.get("is_mandatory") or not spec.get("is_active", 1):
			continue
		row = doc.append(SPEC_TABLE, {"specification": specification, "source": domain.CATEGORY})
		_apply_default(row, spec)


def _apply_default(row, spec: dict):
	default_value = spec.get("default_value")
	if default_value in (None, ""):
		return
	field_type = spec.get("field_type")
	field = domain.VALUE_FIELD_BY_TYPE.get(field_type)
	if not field:
		return
	if field_type == domain.MULTISELECT:
		default_value = domain.serialize_multi_value(domain.parse_multi_value(default_value))
	row.set(field, default_value)


def _reject_duplicates(rows):
	names = [str(row.specification or "").strip() for row in rows if row.specification]
	repeated = domain.repeated_values(names)
	if repeated:
		frappe.throw(_("Характеристика повторюється в товарі: {0}").format(", ".join(repeated)))


def _fill_metadata(rows, masters: dict, category: dict):
	"""Copy master data server-side.

	``fetch_from`` fills these on the form, but Data Import and the REST API do not run it,
	and the checks below must not trust whatever the caller happened to send.
	"""
	for row in rows:
		master = masters.get(row.specification)
		if not master:
			frappe.throw(_("Характеристику «{0}» не знайдено в довіднику").format(row.specification))
		in_category = category.get(row.specification)
		row.spec_label = master.get("spec_name")
		row.field_type = master.get("field_type")
		row.unit = master.get("unit")
		row.is_mandatory = int(in_category.get("is_mandatory") or 0) if in_category else 0
		row.source = domain.CATEGORY if in_category else domain.MANUAL


def _require_mandatory(rows):
	missing = [
		row.spec_label or row.specification
		for row in rows
		if int(row.is_mandatory or 0)
		and domain.is_blank(row.field_type, row.get(domain.value_field_for(row.field_type)))
	]
	if missing:
		# One message with the full list: filling them one throw at a time is unusable
		# when a category defines a dozen mandatory specifications.
		frappe.throw(
			_("Заповніть обов'язкові характеристики: {0}").format(", ".join(missing)),
			title=_("Не заповнено характеристики"),
		)


def _validate_values(rows, masters: dict):
	for row in rows:
		master = masters[row.specification]
		field = domain.value_field_for(row.field_type)
		_clear_unused_columns(row, field)
		value = row.get(field)
		if domain.is_blank(row.field_type, value):
			continue
		if row.field_type in domain.NUMERIC_TYPES:
			_validate_number(row, master, field)
		elif row.field_type == domain.SELECT:
			_validate_select(row, master, value)
		elif row.field_type == domain.MULTISELECT:
			_validate_multiselect(row, master, field, value)
		elif row.field_type == domain.DATE:
			_validate_date(row, value)


def _clear_unused_columns(row, field: str):
	"""Keep exactly one populated column per row so reports and display_value stay unambiguous."""
	for candidate in domain.VALUE_FIELDS:
		if candidate != field and row.get(candidate) not in (None, "", 0):
			row.set(candidate, None)


def _validate_number(row, master: dict, field: str):
	if row.field_type == domain.FLOAT:
		row.set(field, domain.round_to_precision(row.get(field), master.get("spec_precision")))
	minimum, maximum = domain.configured_bounds(master.get("min_value"), master.get("max_value"))
	if domain.is_out_of_range(row.get(field), minimum, maximum):
		frappe.throw(
			_("«{0}»: значення {1} поза дозволеним діапазоном ({2})").format(
				_label(row), row.get(field), _range_text(minimum, maximum)
			)
		)


def _validate_select(row, master: dict, value):
	# Inactive options stay acceptable: deactivating an option must not invalidate the
	# items that already carry it.
	allowed = [option["value"] for option in master.get("options") or []]
	if domain.unknown_options([str(value).strip()], allowed):
		frappe.throw(
			_("«{0}»: значення «{1}» відсутнє у списку дозволених").format(_label(row), value)
		)


def _validate_multiselect(row, master: dict, field: str, value):
	values = domain.parse_multi_value(value)
	repeated = domain.repeated_values(values)
	if repeated:
		frappe.throw(_("«{0}»: значення повторюються — {1}").format(_label(row), ", ".join(repeated)))
	allowed = [option["value"] for option in master.get("options") or []]
	unknown = domain.unknown_options(values, allowed)
	if unknown:
		frappe.throw(
			_("«{0}»: значення відсутні у списку дозволених — {1}").format(_label(row), ", ".join(unknown))
		)
	row.set(field, domain.serialize_multi_value(values))


def _validate_date(row, value):
	try:
		frappe.utils.getdate(value)
	except (ValueError, TypeError):
		frappe.throw(_("«{0}»: некоректна дата «{1}»").format(_label(row), value))


def _write_display_values(rows, masters: dict):
	for row in rows:
		master = masters[row.specification]
		labels = {option["value"]: option["label"] for option in master.get("options") or []}
		row.display_value = domain.format_display_value(
			row.field_type,
			row.get(domain.value_field_for(row.field_type)),
			unit=master.get("unit"),
			spec_precision=master.get("spec_precision"),
			option_labels=labels,
		)


def _label(row) -> str:
	return row.spec_label or row.specification


def _range_text(minimum, maximum) -> str:
	if minimum is not None and maximum is not None:
		return _("від {0} до {1}").format(minimum, maximum)
	if minimum is not None:
		return _("не менше {0}").format(minimum)
	return _("не більше {0}").format(maximum)
