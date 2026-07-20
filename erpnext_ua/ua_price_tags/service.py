"""Server API for immutable price-tag snapshots and print packets."""

from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, formatdate, getdate, now_datetime

from erpnext_ua.ua_pos.barcode import code128_svg_data_uri
from erpnext_ua.ua_price_tags.domain import PROMOTIONAL, choose_price, copies_for, job_group_key


SUPPORTED_SOURCES = {"Purchase Receipt", "Stock Entry", "Delivery Note", "Item"}
AUTO_TEMPLATE_MODE = "Auto Price Tag"
PACKAGING_TEMPLATE_MODE = "Packaging Label"
PACKAGING = "Packaging"
TEMPLATE_MODES = {AUTO_TEMPLATE_MODE, PACKAGING_TEMPLATE_MODE}
REASON_BY_SOURCE = {
	"Purchase Receipt": "Goods Receipt",
	"Stock Entry": "Stock Transfer",
	"Delivery Note": "Goods Delivery",
	"Item": "Manual Print",
}


def _parse(value):
	return frappe.parse_json(value) if isinstance(value, str) else value


def _check_create_permission():
	if not frappe.has_permission("Price Tag Print Job", "create"):
		frappe.throw(_("Недостатньо прав для створення пакетів цінників"), frappe.PermissionError)


def _configuration(
	price_list: str | None = None,
	promotional_price_list: str | None = None,
	company: str | None = None,
	label_size: str | None = None,
):
	settings = frappe.get_single("Price Tag Settings")
	regular = price_list or settings.default_price_list or frappe.get_single_value(
		"Selling Settings", "selling_price_list"
	)
	if not regular:
		frappe.throw("Вкажіть роздрібний прайс-лист у Price Tag Settings")
	if not frappe.db.get_value("Price List", regular, "selling"):
		frappe.throw(f"Прайс-лист {regular} не є прайс-листом продажу")
	currency = frappe.db.get_value("Price List", regular, "currency")
	promo = promotional_price_list if promotional_price_list is not None else settings.promotional_price_list
	if promo and promo == regular:
		frappe.throw("Акційний і роздрібний прайс-листи мають відрізнятися")
	if promo and not frappe.db.get_value("Price List", promo, "selling"):
		frappe.throw(f"Прайс-лист {promo} не є прайс-листом продажу")
	if promo and frappe.db.get_value("Price List", promo, "currency") != currency:
		frappe.throw("Роздрібний та акційний прайс-листи повинні мати однакову валюту")
	return frappe._dict(
		{
			"company": company or settings.default_company or frappe.defaults.get_user_default("Company"),
			"price_list": regular,
			"promotional_price_list": promo,
			"currency": currency,
			"label_size": label_size or settings.default_label_size or "40×25 mm",
			"default_copies": max(1, int(settings.default_copies or 1)),
			"standard_print_format": settings.standard_print_format or "Цінник звичайний 40x25",
			"promotional_print_format": settings.promotional_print_format or "Цінник акційний 40x25",
			"packaging_print_format": settings.packaging_print_format or "Етикетка на упаковку 40x25",
		}
	)


@frappe.whitelist()
def get_configuration():
	config = _configuration()
	return dict(config)


def _item_price(item_code: str, price_list: str | None, uom: str, price_date):
	if not price_list:
		return None
	from erpnext.stock.get_item_details import get_item_price

	rows = get_item_price(
		{
			"price_list": price_list,
			"uom": uom,
			"transaction_date": getdate(price_date),
			"customer": None,
			"supplier": None,
			"batch_no": None,
		},
		item_code,
		ignore_party=False,
	)
	if not rows:
		return None
	row = rows[0]
	details = frappe.db.get_value(
		"Item Price",
		row.name,
		["currency", "valid_from", "valid_upto"],
		as_dict=True,
	) or frappe._dict()
	return frappe._dict(
		{
			"name": row.name,
			"rate": flt(row.price_list_rate),
			"currency": details.currency,
			"valid_from": details.valid_from,
			"valid_upto": details.valid_upto,
		}
	)


def _barcode(item_code: str, uom: str, source_barcode: str | None = None):
	if source_barcode:
		return source_barcode
	value = frappe.db.get_value(
		"Item Barcode",
		{"parent": item_code, "parenttype": "Item", "uom": uom},
		"barcode",
	)
	return value or frappe.db.get_value(
		"Item Barcode", {"parent": item_code, "parenttype": "Item"}, "barcode"
	)


def _variant_text(item):
	return ", ".join(
		str(row.attribute_value) for row in (item.get("attributes") or []) if row.attribute_value
	)


def resolve_item_snapshot(
	item_code: str,
	config,
	*,
	uom: str | None = None,
	warehouse: str | None = None,
	source_barcode: str | None = None,
	price_date=None,
):
	"""Resolve all live data once; callers persist the returned values unchanged."""
	item = frappe.get_cached_doc("Item", item_code)
	if item.disabled:
		frappe.throw(f"Товар {item_code} вимкнено")
	uom = uom or item.stock_uom
	price_date = getdate(price_date)
	regular = _item_price(item_code, config.price_list, uom, price_date)
	promotional = _item_price(item_code, config.promotional_price_list, uom, price_date)
	template_type, selling_price = choose_price(
		regular.rate if regular else None,
		promotional.rate if promotional else None,
	)
	barcode = _barcode(item_code, uom, source_barcode)
	try:
		barcode_svg = code128_svg_data_uri(barcode) if barcode else None
	except ValueError:
		barcode_svg = None
	stock_qty = (
		flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty"))
		if warehouse
		else 0
	)
	is_promotional = template_type == PROMOTIONAL
	return {
		"item_code": item.name,
		"item_name": item.item_name or item.name,
		"barcode": barcode,
		"barcode_svg": barcode_svg,
		"uom": uom,
		"variant_text": _variant_text(item),
		"stock_qty": stock_qty,
		"regular_price": regular.rate if regular else None,
		"selling_price": selling_price,
		"old_price": regular.rate if is_promotional and regular else None,
		"currency": (regular.currency if regular else None) or config.currency,
		"is_promotional": int(is_promotional),
		"template_type": template_type,
		"promotion_from": promotional.valid_from if is_promotional else None,
		"promotion_upto": promotional.valid_upto if is_promotional else None,
		"promotion_text": (
			f"Акційна ціна до {formatdate(promotional.valid_upto, 'dd.MM.yyyy')}"
			if is_promotional and promotional.valid_upto
			else None
		),
		"item_price": regular.name if regular else None,
		"promotional_item_price": promotional.name if is_promotional else None,
		"pricing_rule": None,
		"warehouse": warehouse,
	}


def _source_rows(source_doctype: str, source_name: str, warehouse: str | None = None):
	if source_doctype not in SUPPORTED_SOURCES:
		frappe.throw(f"Джерело {source_doctype} ще не підтримується")
	doc = frappe.get_doc(source_doctype, source_name)
	doc.check_permission("read")
	if source_doctype == "Stock Entry" and doc.get("purpose") != "Material Transfer":
		frappe.throw("Цінники зі Stock Entry доступні лише для Material Transfer")
	if source_doctype == "Item":
		return doc, [
			{
				"key": doc.name,
				"source_row": doc.name,
				"item_code": doc.name,
				"uom": doc.stock_uom,
				"source_qty": 1,
				"warehouse": warehouse,
				"barcode": None,
			}
		]

	rows = []
	for row in doc.get("items") or []:
		row_warehouse = warehouse
		row_uom = row.get("uom") or row.get("stock_uom")
		if source_doctype == "Stock Entry":
			row_warehouse = row.get("t_warehouse") or doc.get("to_warehouse") or warehouse
			row_uom = row.get("stock_uom") or row.get("uom")
		elif source_doctype == "Purchase Receipt":
			row_uom = row.get("stock_uom") or row.get("uom")
		else:
			row_warehouse = row.get("warehouse") or warehouse
		rows.append(
			{
				"key": row.name,
				"source_row": row.name,
				"item_code": row.item_code,
				"uom": row_uom,
				"source_qty": abs(
					flt(row.get("stock_qty") or row.get("transfer_qty") or row.get("qty") or 1)
				),
				"warehouse": row_warehouse,
				"barcode": row.get("barcode"),
			}
		)
	return doc, rows


def _reason(source_doctype: str, doc):
	if source_doctype in {"Purchase Receipt", "Delivery Note"} and doc.get("is_return"):
		return "Return"
	return REASON_BY_SOURCE[source_doctype]


def _apply_template_mode(snapshot, template_mode):
	template_mode = template_mode or AUTO_TEMPLATE_MODE
	if template_mode not in TEMPLATE_MODES:
		frappe.throw(f"Невідомий режим шаблону: {template_mode}")
	if template_mode == PACKAGING_TEMPLATE_MODE:
		snapshot.update(
			{
				"template_type": PACKAGING,
				"selling_price": None,
				"old_price": None,
				"is_promotional": 0,
				"promotion_from": None,
				"promotion_upto": None,
				"promotion_text": None,
				"promotional_item_price": None,
			}
		)
	return snapshot


def _resolved_source_rows(
	source_doctype,
	source_name,
	config,
	warehouse=None,
	price_date=None,
	template_mode=AUTO_TEMPLATE_MODE,
):
	doc, source_rows = _source_rows(source_doctype, source_name, warehouse)
	resolved = []
	for source in source_rows:
		snapshot = resolve_item_snapshot(
			source["item_code"],
			config,
			uom=source["uom"],
			warehouse=source["warehouse"],
			source_barcode=source["barcode"],
			price_date=price_date,
		)
		_apply_template_mode(snapshot, template_mode)
		snapshot.update(source)
		resolved.append(snapshot)
	return doc, resolved


@frappe.whitelist()
def preview_source(
	source_doctype: str,
	source_name: str,
	warehouse: str | None = None,
	price_list: str | None = None,
	promotional_price_list: str | None = None,
	label_size: str | None = None,
	template_mode: str = AUTO_TEMPLATE_MODE,
):
	config = _configuration(price_list, promotional_price_list, label_size=label_size)
	doc, rows = _resolved_source_rows(
		source_doctype,
		source_name,
		config,
		warehouse,
		template_mode=template_mode,
	)
	for row in rows:
		row["copies"] = config.default_copies
		row["price_missing"] = template_mode != PACKAGING_TEMPLATE_MODE and row["selling_price"] is None
	return {"config": dict(config), "company": doc.get("company") or config.company, "rows": rows}


def _insert_jobs(
	resolved_rows,
	config,
	*,
	company=None,
	reason="Manual Print",
	source_doctype=None,
	source_name=None,
	source_label=None,
	reprint_of=None,
):
	groups = defaultdict(list)
	for row in resolved_rows:
		groups[job_group_key(row, config.label_size)].append(row)

	created = []
	for (_warehouse, template_type, _label_size), rows in groups.items():
		if template_type == PACKAGING:
			print_format = config.packaging_print_format
		elif template_type == PROMOTIONAL:
			print_format = config.promotional_print_format
		else:
			print_format = config.standard_print_format
		job = frappe.get_doc(
			{
				"doctype": "Price Tag Print Job",
				"status": "Ready",
				"template_type": template_type,
				"label_size": config.label_size,
				"print_method": "PDF",
				"print_format": print_format,
				"company": company or config.company,
				"warehouse": rows[0].get("warehouse"),
				"price_list": config.price_list,
				"promotional_price_list": config.promotional_price_list,
				"currency": rows[0].get("currency") or config.currency,
				"reason": reason,
				"source_doctype": source_doctype,
				"source_name": source_name,
				"source_label": source_label,
				"reprint_of": reprint_of,
				"ready_at": now_datetime(),
			}
		)
		for row in rows:
			job.append(
				"items",
				{
					"item_code": row.get("item_code"),
					"item_name": row.get("item_name"),
					"barcode": row.get("barcode"),
					"barcode_svg": row.get("barcode_svg"),
					"uom": row.get("uom"),
					"variant_text": row.get("variant_text"),
					"copies": row.get("copies") or 1,
					"stock_qty": row.get("stock_qty"),
					"regular_price": row.get("regular_price"),
					"selling_price": row.get("selling_price"),
					"old_price": row.get("old_price"),
					"currency": row.get("currency") or config.currency,
					"is_promotional": row.get("is_promotional"),
					"promotion_from": row.get("promotion_from"),
					"promotion_upto": row.get("promotion_upto"),
					"promotion_text": row.get("promotion_text"),
					"item_price": row.get("item_price"),
					"promotional_item_price": row.get("promotional_item_price"),
					"pricing_rule": row.get("pricing_rule"),
					"source_row": row.get("source_row"),
					"source_warehouse": row.get("warehouse"),
				},
			)
		job.insert()
		created.append(job.name)
	return created


@frappe.whitelist()
def create_source_jobs(
	source_doctype: str,
	source_name: str,
	selected=None,
	copies_mode: str = "One",
	warehouse: str | None = None,
	price_list: str | None = None,
	promotional_price_list: str | None = None,
	label_size: str | None = None,
	template_mode: str = AUTO_TEMPLATE_MODE,
):
	_check_create_permission()
	config = _configuration(price_list, promotional_price_list, label_size=label_size)
	doc, rows = _resolved_source_rows(
		source_doctype,
		source_name,
		config,
		warehouse,
		template_mode=template_mode,
	)
	selection_payload = _parse(selected) if selected is not None else None
	selection = {str(row.get("source_row")): row for row in (selection_payload or [])}
	if selection_payload is not None:
		rows = [row for row in rows if str(row["source_row"]) in selection]
	if not rows:
		frappe.throw("Оберіть хоча б один товар")
	missing = [
		row["item_code"]
		for row in rows
		if template_mode != PACKAGING_TEMPLATE_MODE and row["selling_price"] is None
	]
	if missing:
		frappe.throw("Немає чинної роздрібної ціни: " + ", ".join(missing))
	for row in rows:
		requested = selection.get(str(row["source_row"]), {}).get("copies") if selection else None
		row["copies"] = copies_for(copies_mode, row.get("source_qty"), requested or config.default_copies)
	return _insert_jobs(
		rows,
		config,
		company=doc.get("company") or config.company,
		reason=_reason(source_doctype, doc),
		source_doctype=source_doctype,
		source_name=source_name,
		source_label=f"{source_doctype} {source_name}",
	)


@frappe.whitelist()
def create_item_jobs(
	items,
	warehouse: str,
	price_list: str | None = None,
	promotional_price_list: str | None = None,
	label_size: str | None = None,
	template_mode: str = AUTO_TEMPLATE_MODE,
):
	_check_create_permission()
	warehouse_doc = frappe.get_doc("Warehouse", warehouse)
	warehouse_doc.check_permission("read")
	config = _configuration(
		price_list,
		promotional_price_list,
		company=warehouse_doc.company,
		label_size=label_size,
	)
	resolved = []
	for selected in _parse(items) or []:
		item_code = selected.get("item_code")
		if not item_code:
			continue
		snapshot = resolve_item_snapshot(
			item_code,
			config,
			uom=selected.get("uom"),
			warehouse=warehouse,
		)
		_apply_template_mode(snapshot, template_mode)
		if template_mode != PACKAGING_TEMPLATE_MODE and snapshot["selling_price"] is None:
			frappe.throw(f"Немає чинної роздрібної ціни: {item_code}")
		snapshot.update(
			{
				"copies": copies_for("Manual Copies", 1, selected.get("copies")),
				"source_row": None,
			}
		)
		resolved.append(snapshot)
	if not resolved:
		frappe.throw("Оберіть хоча б один товар")
	return _insert_jobs(
		resolved,
		config,
		company=warehouse_doc.company,
		reason="Inventory Selection",
		source_label=f"Залишки: {warehouse}",
	)


@frappe.whitelist()
def get_stock_items(
	warehouse: str,
	search_text: str | None = None,
	brand: str | None = None,
	item_group: str | None = None,
	price_list: str | None = None,
	promotional_price_list: str | None = None,
	limit: int = 100,
):
	warehouse_doc = frappe.get_doc("Warehouse", warehouse)
	warehouse_doc.check_permission("read")
	config = _configuration(price_list, promotional_price_list, company=warehouse_doc.company)
	limit = max(1, min(int(limit or 100), 200))
	bins = frappe.get_all(
		"Bin",
		filters={"warehouse": warehouse, "actual_qty": (">", 0)},
		fields=["item_code", "actual_qty"],
		order_by="actual_qty desc",
		limit_page_length=500,
	)
	quantities = {row.item_code: flt(row.actual_qty) for row in bins}
	if not quantities:
		return []
	item_filters = {"name": ("in", list(quantities)), "disabled": 0}
	if brand:
		item_filters["brand"] = brand
	if item_group:
		item_filters["item_group"] = item_group
	items = frappe.get_all(
		"Item",
		filters=item_filters,
		fields=["name", "item_name", "stock_uom", "brand", "item_group"],
		limit_page_length=500,
	)
	needle = str(search_text or "").strip().casefold()
	last_printed = {
		row.item_code: row.printed_at
		for row in frappe.db.sql(
			"""
			select child.item_code, max(job.printed_at) as printed_at
			from `tabPrice Tag Print Job Item` child
			inner join `tabPrice Tag Print Job` job on job.name = child.parent
			where job.status = 'Printed' and job.warehouse = %s
			group by child.item_code
			""",
			warehouse,
			as_dict=True,
		)
	}
	result = []
	for item in items:
		if len(result) >= limit:
			break
		snapshot = resolve_item_snapshot(item.name, config, uom=item.stock_uom, warehouse=warehouse)
		if needle and needle not in " ".join(
			str(value or "").casefold() for value in (item.name, item.item_name, snapshot["barcode"])
		):
			continue
		result.append(
			{
				"item_code": item.name,
				"item_name": item.item_name,
				"uom": item.stock_uom,
				"brand": item.brand,
				"item_group": item.item_group,
				"barcode": snapshot["barcode"],
				"stock_qty": quantities[item.name],
				"regular_price": snapshot["regular_price"],
				"selling_price": snapshot["selling_price"],
				"is_promotional": snapshot["is_promotional"],
				"currency": snapshot["currency"],
				"last_printed_at": last_printed.get(item.name),
			}
		)
	return result


@frappe.whitelist()
def repeat_print_job(job_name: str):
	_check_create_permission()
	source = frappe.get_doc("Price Tag Print Job", job_name)
	source.check_permission("read")
	if source.status not in {"Ready", "Printed"}:
		frappe.throw("Повторити можна лише зафіксований пакет")
	config = frappe._dict(
		{
			"company": source.company,
			"price_list": source.price_list,
			"promotional_price_list": source.promotional_price_list,
			"currency": source.currency,
			"label_size": source.label_size,
			"standard_print_format": source.print_format,
			"promotional_print_format": source.print_format,
			"packaging_print_format": source.print_format,
		}
	)
	rows = []
	for item in source.items:
		row = {field: item.get(field) for field in (
			"item_code", "item_name", "barcode", "barcode_svg", "uom", "variant_text", "copies",
			"stock_qty", "regular_price", "selling_price", "old_price", "currency", "is_promotional",
			"promotion_from", "promotion_upto", "promotion_text", "item_price", "promotional_item_price",
			"pricing_rule", "source_row",
		)}
		row["warehouse"] = item.source_warehouse or source.warehouse
		row["template_type"] = source.template_type
		rows.append(row)
	return _insert_jobs(
		rows,
		config,
		company=source.company,
		reason="Reprint",
		source_doctype=source.source_doctype,
		source_name=source.source_name,
		source_label=f"Повторення {source.name}",
		reprint_of=source.name,
	)[0]
