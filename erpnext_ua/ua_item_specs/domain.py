"""Pure item-specification rules shared by the controllers, API and unit tests."""

from __future__ import annotations

import json
import re
from datetime import date, datetime


DATA = "Data"
TEXT = "Text"
HTML = "HTML"
INT = "Int"
FLOAT = "Float"
SELECT = "Select"
MULTISELECT = "MultiSelect"
CHECK = "Check"
DATE = "Date"

FIELD_TYPES = (DATA, TEXT, HTML, INT, FLOAT, SELECT, MULTISELECT, CHECK, DATE)
OPTION_TYPES = (SELECT, MULTISELECT)
NUMERIC_TYPES = (INT, FLOAT)

# Every type stores its value in its own column. A single shared text column would be
# shorter, but then numbers are text: `довжина > 50` stops working in reports and sorting
# puts 9 after 100. The typed columns exist for the report layer, not for the form.
VALUE_FIELD_BY_TYPE = {
	DATA: "value_data",
	TEXT: "value_text",
	HTML: "value_html",
	INT: "value_int",
	FLOAT: "value_float",
	SELECT: "value_select",
	MULTISELECT: "value_multi",
	CHECK: "value_check",
	DATE: "value_date",
}
VALUE_FIELDS = tuple(VALUE_FIELD_BY_TYPE.values())

CATEGORY = "Category"
MANUAL = "Manual"

YES = "Так"
NO = "Ні"

FIELDNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
DISPLAY_LIMIT = 140
_TAG_PATTERN = re.compile(r"<[^>]+>")
_SPACE_PATTERN = re.compile(r"\s+")


def value_field_for(field_type: str) -> str:
	"""Column that stores a value of ``field_type``."""
	try:
		return VALUE_FIELD_BY_TYPE[field_type]
	except KeyError:
		raise ValueError(f"Unknown specification type: {field_type!r}") from None


def is_valid_fieldname(value: str | None) -> bool:
	return bool(FIELDNAME_PATTERN.match(str(value or "")))


def parse_multi_value(raw) -> list[str]:
	"""Read a MultiSelect payload as a list of values.

	Stored form is a JSON array. A comma-separated string is also accepted so that Data
	Import stays usable by hand; values are checked against the option list afterwards,
	so a wrong guess surfaces as a validation error rather than as silent data.
	"""
	if raw is None or raw == "":
		return []
	if isinstance(raw, (list, tuple)):
		return [str(item).strip() for item in raw if str(item).strip()]
	try:
		parsed = json.loads(raw)
	except (TypeError, ValueError):
		return [part.strip() for part in str(raw).split(",") if part.strip()]
	if isinstance(parsed, (list, tuple)):
		return [str(item).strip() for item in parsed if str(item).strip()]
	text = str(parsed).strip()
	return [text] if text else []


def serialize_multi_value(values) -> str:
	return json.dumps(list(values), ensure_ascii=False)


def is_blank(field_type: str, value) -> bool:
	"""Whether a stored value counts as "not filled in" for a mandatory specification.

	``Check`` is never blank — an unticked box is a real answer ("Ні"), not a missing one.
	``Int``/``Float`` treat ``0`` as filled: Frappe has no empty state for numbers, so an
	untouched field arrives as ``0``. Rejecting zero would reject a legitimate measurement.
	"""
	if field_type == CHECK:
		return False
	if value is None:
		return True
	if field_type == MULTISELECT:
		return not parse_multi_value(value)
	return str(value).strip() == ""


def configured_bounds(min_value, max_value):
	"""Bounds that are actually in force.

	Frappe has no empty state for ``Float``: an unconfigured bound arrives as ``0``, which
	cannot be told apart from a deliberate zero. ``0`` therefore means "no limit" — stated
	on the field description, and the reason a "не менше нуля" rule has to be expressed
	with a small positive minimum instead.
	"""
	minimum = float(min_value or 0) or None
	maximum = float(max_value or 0) or None
	return minimum, maximum


def is_out_of_range(value, min_value, max_value) -> bool:
	if value is None:
		return False
	number = float(value)
	if min_value is not None and number < float(min_value):
		return True
	return max_value is not None and number > float(max_value)


def round_to_precision(value, spec_precision):
	"""Round only when a precision is configured; an unset precision leaves the value alone."""
	if value is None:
		return None
	digits = int(spec_precision or 0)
	return round(float(value), digits) if digits > 0 else float(value)


def unknown_options(values, allowed) -> list[str]:
	"""Values that are not in the specification's option list, in the order given."""
	permitted = set(allowed or ())
	return [value for value in values if value not in permitted]


def repeated_values(values) -> list[str]:
	seen: set[str] = set()
	repeated: list[str] = []
	for value in values:
		if value in seen and value not in repeated:
			repeated.append(value)
		seen.add(value)
	return repeated


def merge_group_specifications(levels) -> list[dict]:
	"""Merge category rows from root to leaf, letting a descendant override its ancestors.

	``levels`` is ordered root → leaf as ``(item_group, rows)`` pairs. A specification keeps
	the position of its *first* declaration, so inherited rows stay above the category's own
	additions, while its values come from the *closest* descendant that declared it — that is
	what lets a subcategory make an inherited specification mandatory without redeclaring the
	whole set.
	"""
	merged: dict[str, dict] = {}
	for item_group, rows in levels:
		for row in rows:
			specification = str(row.get("specification") or "").strip()
			if not specification:
				continue
			values = {
				"specification": specification,
				"is_mandatory": int(row.get("is_mandatory") or 0),
				"default_value": row.get("default_value"),
				"source_item_group": item_group,
			}
			existing = merged.get(specification)
			if existing is None:
				merged[specification] = values
			else:
				existing.update(values)
	return list(merged.values())


def format_display_value(
	field_type: str,
	value,
	*,
	unit: str | None = None,
	spec_precision=None,
	option_labels: dict | None = None,
) -> str:
	"""Human-readable rendering used by the grid, search and reports."""
	if field_type == CHECK:
		return YES if _as_int(value) else NO
	if is_blank(field_type, value):
		return ""
	if field_type == INT:
		return _with_unit(str(_as_int(value)), unit)
	if field_type == FLOAT:
		return _with_unit(_format_float(value, spec_precision), unit)
	if field_type == SELECT:
		return _label_for(str(value).strip(), option_labels)
	if field_type == MULTISELECT:
		return ", ".join(_label_for(item, option_labels) for item in parse_multi_value(value))
	if field_type == DATE:
		return _format_date(value)
	if field_type in (TEXT, HTML):
		return _truncate(_strip_html(str(value)))
	return _truncate(str(value).strip())


def _as_int(value) -> int:
	try:
		return int(float(value or 0))
	except (TypeError, ValueError):
		return 0


def _with_unit(text: str, unit: str | None) -> str:
	unit = str(unit or "").strip()
	return f"{text} {unit}" if unit else text


def _format_float(value, spec_precision) -> str:
	digits = int(spec_precision or 0)
	if digits > 0:
		return f"{float(value):.{digits}f}"
	text = f"{float(value):.6f}".rstrip("0").rstrip(".")
	return text or "0"


def _label_for(value: str, option_labels: dict | None) -> str:
	return str((option_labels or {}).get(value) or value)


def _strip_html(text: str) -> str:
	return _SPACE_PATTERN.sub(" ", _TAG_PATTERN.sub(" ", text)).strip()


def _truncate(text: str) -> str:
	if len(text) <= DISPLAY_LIMIT:
		return text
	return text[: DISPLAY_LIMIT - 1].rstrip() + "…"


def _format_date(value) -> str:
	if isinstance(value, datetime):
		value = value.date()
	if isinstance(value, date):
		return value.strftime("%d.%m.%Y")
	text = str(value).strip()
	try:
		return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
	except ValueError:
		return text
