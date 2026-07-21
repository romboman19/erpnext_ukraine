"""Controlled Ukrainian Purchase Receipt completion workflow."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate, now_datetime

from erpnext_ua.ua_receiving.domain import resolve_receipt_warehouse, suggest_selling_price


def _normal_supplier_receipt(doc) -> bool:
	return not doc.get("is_return") and not doc.get("is_internal_supplier")


def _buying_setting(fieldname, default=None):
	value = frappe.db.get_single_value("Buying Settings", fieldname)
	return default if value in (None, "") else value


def _stock_item(item_code: str) -> bool:
	return bool(frappe.get_cached_value("Item", item_code, "is_stock_item"))


def validate_purchase_receipt(doc, method=None):
	"""Enforce Ukrainian primary-document evidence before a normal receipt is submitted."""
	if not _normal_supplier_receipt(doc):
		return

	required = {
		"supplier_delivery_note": _("Номер документа постачальника"),
		"ua_supplier_document_type": _("Тип документа постачальника"),
		"ua_supplier_document_date": _("Дата документа постачальника"),
		"ua_received_by": _("Прийняв товар"),
	}
	missing = [label for fieldname, label in required.items() if not doc.get(fieldname)]
	if missing:
		frappe.throw(_("Заповніть обов'язкові реквізити приймання: {0}").format(", ".join(missing)))
	if not doc.get("ua_receipt_verified"):
		frappe.throw(_("Підтвердіть звірку фактичної кількості з документом постачальника"))
	if _buying_setting("ua_require_supplier_document_attachment", 1) and not doc.get(
		"ua_supplier_document_file"
	):
		frappe.throw(_("Додайте скан або електронний файл документа постачальника"))

	duplicate = frappe.db.get_value(
		"Purchase Receipt",
		{
			"supplier": doc.supplier,
			"supplier_delivery_note": doc.supplier_delivery_note,
			"docstatus": 1,
			"name": ("!=", doc.name),
		},
		"name",
	)
	if duplicate:
		frappe.throw(
			_("Документ постачальника {0} вже використано у прихідній накладній {1}").format(
				doc.supplier_delivery_note, frappe.bold(duplicate)
			)
		)

	for row in doc.get("items") or []:
		if not _stock_item(row.item_code):
			continue
		warehouse = resolve_receipt_warehouse(row.get("warehouse"), doc.get("set_warehouse"))
		if flt(row.get("qty")) > 0 and not warehouse:
			frappe.throw(_("Рядок {0}: вкажіть склад прийнятого товару").format(row.idx))
		if flt(row.get("rejected_qty")) > 0 and not (
			row.get("rejected_warehouse") or doc.get("rejected_warehouse")
		):
			frappe.throw(_("Рядок {0}: для браку вкажіть окремий склад відхиленого товару").format(row.idx))
		if flt(row.get("qty")) > 0 and flt(row.get("base_rate")) <= 0 and not row.get(
			"allow_zero_valuation_rate"
		):
			frappe.throw(_("Рядок {0}: закупівельна ціна має бути більшою за нуль").format(row.idx))


def _active_item_price(item_code: str, price_list: str, uom: str, on_date):
	from erpnext.stock.get_item_details import get_item_price

	rows = get_item_price(
		{
			"price_list": price_list,
			"uom": uom,
			"transaction_date": getdate(on_date),
			"customer": None,
			"supplier": None,
			"batch_no": None,
		},
		item_code,
		ignore_party=False,
	)
	return rows[0] if rows else None


def _receipt_rows(receipt, price_list: str, markup_percent: float, rounding_step: float):
	rows = []
	for item in receipt.items:
		if not _stock_item(item.item_code) or flt(item.qty) <= 0:
			continue
		uom = item.stock_uom or item.uom
		warehouse = resolve_receipt_warehouse(
			item.get("warehouse"), receipt.get("set_warehouse")
		)
		unit_cost = flt(item.get("valuation_rate")) or (
			flt(item.get("base_rate")) / max(flt(item.get("conversion_factor")), 1)
		)
		current = _active_item_price(item.item_code, price_list, uom, nowdate())
		current_rate = flt(current.price_list_rate) if current else None
		suggested = suggest_selling_price(unit_cost, markup_percent, rounding_step)
		if not markup_percent and current_rate:
			suggested = current_rate
		rows.append(
			{
				"source_row": item.name,
				"item_code": item.item_code,
				"item_name": item.item_name,
				"uom": uom,
				"warehouse": warehouse,
				"received_qty": flt(item.qty),
				"unit_cost": unit_cost,
				"current_price": current_rate,
				"suggested_price": suggested,
				"copies": max(1, int(flt(item.stock_qty or item.qty))),
			}
		)
	return rows


def _require_submitted_receipt(receipt_name: str):
	receipt = frappe.get_doc("Purchase Receipt", receipt_name)
	receipt.check_permission("read")
	if receipt.docstatus != 1:
		frappe.throw(_("Завершення приймання доступне лише для проведеної прихідної накладної"))
	if receipt.get("is_return"):
		frappe.throw(_("Повернення постачальнику не використовує сценарій завершення приймання"))
	return receipt


def _selling_price_list(price_list: str | None = None):
	price_list = price_list or frappe.get_single_value("Selling Settings", "selling_price_list")
	if not price_list:
		frappe.throw(_("Вкажіть роздрібний прайс-лист у Selling Settings"))
	details = frappe.db.get_value("Price List", price_list, ["selling", "enabled", "currency"], as_dict=True)
	if not details or not details.selling or not details.enabled:
		frappe.throw(_("Прайс-лист {0} не є активним роздрібним прайс-листом").format(price_list))
	return price_list, details


@frappe.whitelist()
def preview_receipt_completion(receipt_name: str, price_list: str | None = None):
	receipt = _require_submitted_receipt(receipt_name)
	price_list, price_list_details = _selling_price_list(price_list)
	company_currency = frappe.get_cached_value("Company", receipt.company, "default_currency")
	if price_list_details.currency != company_currency:
		frappe.throw(
			_("Для автоматичного розрахунку валюта прайс-листа має збігатися з валютою компанії")
		)
	markup = flt(_buying_setting("ua_default_retail_markup_percent", 0))
	rounding_step = flt(_buying_setting("ua_retail_price_rounding_step", 1))
	return {
		"receipt": receipt.name,
		"price_list": price_list,
		"currency": price_list_details.currency,
		"markup_percent": markup,
		"rounding_step": rounding_step,
		"create_purchase_invoice": int(
			bool(_buying_setting("ua_create_purchase_invoice_draft", 1))
		),
		"rows": _receipt_rows(receipt, price_list, markup, rounding_step),
	}


def _price_permission_override() -> bool:
	if frappe.has_permission("Item Price", "write"):
		return False
	if {"Price Tag Manager", "System Manager"}.intersection(frappe.get_roles()):
		return True
	frappe.throw(_("Недостатньо прав для зміни роздрібних цін"), frappe.PermissionError)


def _update_item_price(
	item_code: str,
	uom: str,
	price_list: str,
	rate: float,
	receipt_name: str,
	ignore_permissions: bool = False,
):
	if rate <= 0:
		frappe.throw(_("Роздрібна ціна для {0} має бути більшою за нуль").format(item_code))
	current = _active_item_price(item_code, price_list, uom, nowdate())
	if current:
		doc = frappe.get_doc("Item Price", current.name)
		if not ignore_permissions:
			doc.check_permission("write")
		doc.price_list_rate = rate
		doc.note = _("Оновлено із прихідної накладної {0}").format(receipt_name)
		doc.save(ignore_permissions=ignore_permissions)
		return doc.name
	doc = frappe.get_doc(
		{
			"doctype": "Item Price",
			"item_code": item_code,
			"uom": uom,
			"price_list": price_list,
			"price_list_rate": rate,
			"valid_from": nowdate(),
			"note": _("Створено із прихідної накладної {0}").format(receipt_name),
		}
	)
	doc.insert(ignore_permissions=ignore_permissions)
	return doc.name


def _existing_purchase_invoice(receipt_name: str):
	return frappe.db.get_value(
		"Purchase Invoice Item",
		{"purchase_receipt": receipt_name, "docstatus": ("<", 2)},
		"parent",
	)


def _create_purchase_invoice_draft(receipt):
	existing = receipt.get("ua_purchase_invoice") or _existing_purchase_invoice(receipt.name)
	if existing and frappe.db.exists("Purchase Invoice", existing):
		return existing
	from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice

	invoice = make_purchase_invoice(receipt.name)
	invoice.bill_no = receipt.supplier_delivery_note
	invoice.bill_date = receipt.ua_supplier_document_date
	invoice.ua_source_purchase_receipt = receipt.name
	invoice.ua_add_vat_20_to_prices = receipt.get("ua_add_vat_20_to_prices")
	price_without_vat = {row.name: row.get("ua_price_without_vat") for row in receipt.items}
	for row in invoice.items:
		source_row = row.get("pr_detail")
		if source_row in price_without_vat:
			row.ua_price_without_vat = price_without_vat[source_row]
	invoice.insert()
	return invoice.name


@frappe.whitelist()
def complete_receipt(
	receipt_name: str,
	prices,
	price_list: str | None = None,
	create_purchase_invoice: int | str = 1,
):
	"""Approve retail prices, create immutable label jobs, and draft the supplier bill."""
	receipt = _require_submitted_receipt(receipt_name)
	receipt.check_permission("write")
	if receipt.get("ua_receiving_completed"):
		frappe.throw(_("Це приймання вже завершено; для повторного друку використайте пакет цінників"))
	ignore_price_permissions = _price_permission_override()
	price_list, details = _selling_price_list(price_list)
	company_currency = frappe.get_cached_value("Company", receipt.company, "default_currency")
	if details.currency != company_currency:
		frappe.throw(
			_("Валюта роздрібного прайс-листа має збігатися з валютою компанії")
		)
	payload = frappe.parse_json(prices) if isinstance(prices, str) else prices
	payload = payload or []
	allowed = {
		row.name: row
		for row in receipt.items
		if flt(row.qty) > 0 and _stock_item(row.item_code)
	}
	selected = []
	price_by_item_uom = {}
	for row in payload:
		if not row.get("selected"):
			continue
		source_row = str(row.get("source_row") or "")
		item = allowed.get(source_row)
		if not item:
			frappe.throw(_("Рядок прихідної накладної {0} недоступний").format(source_row))
		rate = flt(row.get("new_price"))
		uom = item.stock_uom or item.uom
		key = (item.item_code, uom)
		if key in price_by_item_uom and price_by_item_uom[key] != rate:
			frappe.throw(
				_("Товар {0} з одиницею {1} не може мати дві різні ціни в одному приході").format(
					item.item_code, uom
				)
			)
		if key not in price_by_item_uom:
			_update_item_price(
				item.item_code,
				uom,
				price_list,
				rate,
				receipt.name,
				ignore_permissions=ignore_price_permissions,
			)
			price_by_item_uom[key] = rate
		selected.append({"source_row": source_row, "copies": max(1, int(flt(row.get("copies") or 1)))})
	if not selected:
		frappe.throw(_("Оберіть хоча б один товар і підтвердьте його роздрібну ціну"))

	from erpnext_ua.ua_price_tags.service import create_source_jobs

	jobs = create_source_jobs(
		"Purchase Receipt",
		receipt.name,
		selected=selected,
		copies_mode="Manual Copies",
		price_list=price_list,
	)
	invoice_name = None
	if int(create_purchase_invoice or 0):
		invoice_name = _create_purchase_invoice_draft(receipt)

	frappe.db.set_value(
		"Purchase Receipt",
		receipt.name,
		{
			"ua_receiving_completed": 1,
			"ua_receiving_completed_on": now_datetime(),
			"ua_purchase_invoice": invoice_name,
			"ua_price_tag_jobs": "\n".join(jobs),
		},
		update_modified=False,
	)
	job_prints = [
		{
			"name": name,
			"print_format": frappe.db.get_value("Price Tag Print Job", name, "print_format"),
		}
		for name in jobs
	]
	return {
		"purchase_invoice": invoice_name,
		"price_tag_jobs": jobs,
		"price_tag_prints": job_prints,
	}
