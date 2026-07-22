"""Install editable Print Designer copies without overwriting user layouts."""

from __future__ import annotations

import json
from pathlib import Path

import frappe

from erpnext_ua.print_designer_documents import RECEIPT_FORMAT_NAME, SALES_FORMATS, build_document_formats
from erpnext_ua.print_designer_price_tags import (
	PACKAGING_FORMAT_NAME,
	PRICE_TAG_FORMAT_FIELDS,
	PROMOTIONAL_FORMAT_NAME,
	STANDARD_FORMAT_NAME,
	build_price_tag_formats,
)


DESIGNER_FORMAT_NAMES = (
	STANDARD_FORMAT_NAME,
	PROMOTIONAL_FORMAT_NAME,
	PACKAGING_FORMAT_NAME,
	*(row[0] for row in SALES_FORMATS),
	RECEIPT_FORMAT_NAME,
)


def ensure_print_designer_formats():
	"""Create native editable formats once when Print Designer is installed."""
	if not _print_designer_is_available():
		return

	base_settings = _load_base_settings()
	formats = [
		*build_price_tag_formats(base_settings),
		*build_document_formats(base_settings),
	]
	for values in formats:
		_create_format_if_missing(values)

	_switch_price_tag_defaults()
	frappe.clear_cache(doctype="Print Format")
	frappe.db.commit()


def _print_designer_is_available() -> bool:
	if "print_designer" not in frappe.get_installed_apps():
		return False
	if not frappe.db.table_exists("Print Format"):
		return False
	return bool(frappe.get_meta("Print Format").get_field("print_designer"))


def _load_base_settings() -> dict:
	root = Path(frappe.get_app_path("print_designer", "default_templates", "erpnext"))
	for filename in ("sales_invoice_pd_format_v2.json", "sales_order_pd_v2.json"):
		path = root / filename
		if not path.exists():
			continue
		data = json.loads(path.read_text(encoding="utf-8"))
		return json.loads(data["print_designer_settings"])
	raise FileNotFoundError("Print Designer base template was not found")


def _create_format_if_missing(values: dict):
	name = values["name"]
	if frappe.db.exists("Print Format", name):
		return
	frappe.get_doc(values).insert(ignore_permissions=True)


def _switch_price_tag_defaults():
	if not frappe.db.exists("DocType", "Price Tag Settings"):
		return
	if not all(frappe.db.exists("Print Format", name) for name in DESIGNER_FORMAT_NAMES[:3]):
		return

	settings = frappe.get_single("Price Tag Settings")
	changed = False
	for fieldname, (legacy_name, designer_name) in PRICE_TAG_FORMAT_FIELDS.items():
		if settings.get(fieldname) not in (None, "", legacy_name):
			continue
		settings.set(fieldname, designer_name)
		changed = True
	if changed:
		settings.save(ignore_permissions=True)
