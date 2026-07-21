"""Purchase-price helpers for a Ukrainian non-VAT-payer workflow."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt

from erpnext_ua.ua_receiving.domain import add_vat_20


LEGACY_VAT_TEMPLATE_PREFIXES = (
	"Додати ПДВ 20% до ціни (неплатник)",
	"Ціна вже містить ПДВ 20% (неплатник)",
)
LEGACY_VAT_DESCRIPTION = "Невідшкодовуваний ПДВ 20%"


def apply_supplier_price_vat(doc, method=None):
	"""Convert stored net input to gross item rates without tax rows or tax GL entries."""
	if not cint(doc.get("ua_add_vat_20_to_prices")):
		return

	_remove_legacy_vat(doc)
	for row in doc.get("items") or []:
		_apply_row_price(row)


def _apply_row_price(row):
	net_price = row.get("ua_price_without_vat")
	if net_price in (None, ""):
		frappe.throw(
			_("Рядок {0}: заповніть «Ціна без ПДВ» або вимкніть галочку додавання ПДВ").format(
				row.idx
			)
		)
	if flt(net_price) < 0:
		frappe.throw(_("Рядок {0}: ціна без ПДВ не може бути від'ємною").format(row.idx))

	precision = row.precision("rate") or 2
	gross_price = add_vat_20(net_price, precision)
	row.rate = gross_price
	if row.meta.has_field("price_list_rate"):
		row.price_list_rate = gross_price
	if row.meta.has_field("discount_percentage"):
		row.discount_percentage = 0
	if row.meta.has_field("discount_amount"):
		row.discount_amount = 0


def _remove_legacy_vat(doc):
	template = str(doc.get("taxes_and_charges") or "")
	using_legacy_template = any(template.startswith(prefix) for prefix in LEGACY_VAT_TEMPLATE_PREFIXES)
	if using_legacy_template:
		doc.taxes_and_charges = None

	kept_rows = []
	for row in doc.get("taxes") or []:
		description = str(row.get("description") or "")
		if using_legacy_template or description.startswith(LEGACY_VAT_DESCRIPTION):
			continue
		kept_rows.append(row)
	doc.set("taxes", kept_rows)
