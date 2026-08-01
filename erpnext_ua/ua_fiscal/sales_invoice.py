"""Інтеграція Sales Invoice (POS) → фіскальний чек ПРРО.

При проведенні POS-рахунку створюється й фіскалізується PRRO Receipt через
оркестрацію. Каса визначається за POS Profile рахунку, ключ — за касиром
(власником рахунку) або ключем каси за замовчуванням.
"""

import re

import frappe

from erpnext_ua.ua_fiscal import orchestration as orch
from erpnext_ua.ua_fiscal.fiscal_client import FiscalServerError
from erpnext_ua.ua_fiscal.payment import fiscal_payform_name

# Мапінг типу форми оплати ERPNext → код форми оплати ДПС
PAYFORM_CASH = 0
PAYFORM_CASHLESS = 1


def _register_for_invoice(si) -> str | None:
	if si.get("pos_profile"):
		return frappe.db.get_value(
			"PRRO Cash Register", {"pos_profile": si.pos_profile, "status": "Active"}
		)
	if si.get("ua_ecommerce_channel") and si.get("company"):
		return frappe.db.get_value(
			"PRRO Cash Register",
			{
				"company": si.company,
				"ecommerce_default": 1,
				"status": "Active",
			},
		)
	return None


def _kep_key_for_invoice(si, register_name: str) -> str | None:
	"""Ключ касира (власника рахунку), інакше — ключ каси за замовчуванням."""
	key = frappe.db.get_value("UA KEP Key", {"user": si.owner, "status": "Active"})
	return key or frappe.db.get_value("PRRO Cash Register", register_name, "default_kep_key")


def _invoice_lines(si) -> list[dict]:
	lines = []
	for it in si.get("items") or []:
		item_meta = frappe.db.get_value(
			"Item",
			it.item_code,
			["customs_tariff_number", "ua_prro_dkpp", "ua_prro_unit_code", "ua_prro_tax_letters"],
			as_dict=True,
		) or {}
		net_amount = it.get("net_amount")
		net_rate = it.get("net_rate")
		amount = abs(frappe.utils.flt(net_amount if net_amount is not None else it.get("amount")))
		qty = abs(frappe.utils.flt(it.qty))
		final_rate = abs(frappe.utils.flt(net_rate if net_rate is not None else it.rate))
		gross_rate = abs(frappe.utils.flt(it.get("price_list_rate") or it.get("rate") or final_rate))
		subtotal = frappe.utils.flt(gross_rate * qty, 2)
		discount_sum = frappe.utils.flt(max(0, subtotal - amount), 2)
		line = {
			"code": it.item_code or it.item_name,
			"barcode": it.get("barcode"),
			"uktzed": it.get("customs_tariff_number") or item_meta.get("customs_tariff_number"),
			"dkpp": it.get("ua_prro_dkpp") or item_meta.get("ua_prro_dkpp"),
			"unit_cd": it.get("ua_prro_unit_code") or item_meta.get("ua_prro_unit_code"),
			"letters": it.get("ua_prro_tax_letters") or item_meta.get("ua_prro_tax_letters"),
			"name": it.item_name or it.item_code,
			"uom": it.uom or it.stock_uom or "шт",
			"qty": qty,
			"price": gross_rate if discount_sum else final_rate,
			"amount": amount,
			"loyalty_discount_sum": abs(frappe.utils.flt(it.get("ua_loyalty_redeemed_amount"))),
		}
		if discount_sum:
			line.update(
				{
					"discount_type": 0,
					"subtotal": subtotal,
					"discount_percent": frappe.utils.flt(discount_sum * 100 / subtotal, 2) if subtotal else 0,
					"discount_sum": discount_sum,
				}
			)
		lines.append(line)
	return _group_gsf_lines(si, lines)


def _group_gsf_lines(si, lines: list[dict]) -> list[dict]:
	"""Collapse technical FIFO rows only when their fiscal identity is equal."""
	groups: dict[str, list[dict]] = {}
	order: list[str] = []
	for item, line in zip(si.get("items") or [], lines, strict=True):
		key = item.get("gsf_display_group") or item.name
		if key not in groups:
			groups[key] = []
			order.append(key)
		groups[key].append(line)
	result = []
	for key in order:
		rows = groups[key]
		if len(rows) == 1:
			result.append(rows[0])
			continue
		_assert_same_fiscal_identity(key, rows)
		merged = dict(rows[0])
		merged["qty"] = sum(row["qty"] for row in rows)
		merged["amount"] = frappe.utils.flt(sum(row["amount"] for row in rows), 2)
		merged["loyalty_discount_sum"] = frappe.utils.flt(
			sum(row.get("loyalty_discount_sum") or 0 for row in rows), 2
		)
		if any(row.get("discount_sum") for row in rows):
			merged["subtotal"] = frappe.utils.flt(sum(row.get("subtotal") or 0 for row in rows), 2)
			merged["discount_sum"] = frappe.utils.flt(
				sum(row.get("discount_sum") or 0 for row in rows), 2
			)
			merged["discount_percent"] = (
				frappe.utils.flt(merged["discount_sum"] * 100 / merged["subtotal"], 2)
				if merged["subtotal"]
				else 0
			)
		result.append(merged)
	return result


def _assert_same_fiscal_identity(group: str, rows: list[dict]) -> None:
	fields = ("code", "barcode", "uktzed", "dkpp", "unit_cd", "letters", "name", "uom", "price")
	first = rows[0]
	mismatches = [field for field in fields if any(row.get(field) != first.get(field) for row in rows[1:])]
	if mismatches:
		frappe.throw(
			f"GSF display group {group} cannot be fiscalized as one line: {', '.join(mismatches)} differ"
		)


def _invoice_payments(si) -> list[dict]:
	payments = []
	payment_rows = list(si.get("payments", []))
	if not payment_rows and si.get("ua_ecommerce_channel"):
		payment_rows = _submitted_payment_rows(si.name)
	for p in payment_rows:
		amount = abs(frappe.utils.flt(p.amount))
		if not amount:
			continue
		payment_config = frappe.db.get_value(
			"Mode of Payment",
			p.mode_of_payment,
			["ua_payformcd", "ua_prro_payment_form", "ua_prro_payment_means"],
			as_dict=True,
		) or {}
		configured_code = payment_config.get("ua_payformcd")
		code = (
			int(configured_code)
			if configured_code not in (None, "")
			else (PAYFORM_CASH if (p.type or "").lower() == "cash" else PAYFORM_CASHLESS)
		)
		row = {
			"code": code,
			"name": fiscal_payform_name(None, code, payment_config.get("ua_prro_payment_means") or p.mode_of_payment),
			"form": payment_config.get("ua_prro_payment_form") or ("ГОТІВКА" if code == 0 else "БЕЗГОТІВКОВА"),
			"sum": amount,
		}
		# решта для готівки
		if code == PAYFORM_CASH and frappe.utils.flt(si.get("change_amount")) > 0:
			row["provided"] = amount + frappe.utils.flt(si.change_amount)
			row["remains"] = frappe.utils.flt(si.change_amount)
		payments.append(row)
	if not payments and si.get("ua_ecommerce_channel"):
		frappe.throw(
			f"Для ecommerce-рахунку {si.name} немає проведеної оплати",
			FiscalServerError,
		)
	if not payments:  # legacy non-POS manual fiscalization
		payments.append({"code": PAYFORM_CASH, "name": "ГОТІВКА", "form": "ГОТІВКА",
						 "sum": abs(frappe.utils.flt(si.rounded_total or si.grand_total))})
	if si.get("ua_ecommerce_channel"):
		total = abs(frappe.utils.flt(si.rounded_total or si.grand_total))
		paid = frappe.utils.flt(sum(row["sum"] for row in payments), 2)
		if abs(total - paid) > 0.01:
			frappe.throw(
				f"Оплати ecommerce-рахунку {si.name} ({paid}) не збігаються з підсумком ({total})",
				FiscalServerError,
			)
	return payments


def _submitted_payment_rows(sales_invoice: str) -> list:
	references = frappe.get_all(
		"Payment Entry Reference",
		filters={
			"parenttype": "Payment Entry",
			"reference_doctype": "Sales Invoice",
			"reference_name": sales_invoice,
		},
		fields=["parent", "allocated_amount"],
		order_by="parent asc, idx asc",
	)
	rows = []
	for reference in references:
		payment = frappe.db.get_value(
			"Payment Entry",
			reference.parent,
			["docstatus", "mode_of_payment"],
			as_dict=True,
		)
		if not payment or int(payment.docstatus or 0) != 1:
			continue
		payment_type = frappe.db.get_value("Mode of Payment", payment.mode_of_payment, "type")
		rows.append(
			frappe._dict(
				mode_of_payment=payment.mode_of_payment,
				type=payment_type,
				amount=reference.allocated_amount,
			)
		)
	return rows


def _invoice_taxes(si) -> list[dict]:
	"""Мапить фактичні податки SI, надаючи перевагу явним полям конфігурації ПРРО."""
	result = []
	item_amounts = {}
	for item in si.get("items") or []:
		net_amount = item.get("net_amount")
		item_amounts[item.item_code] = item_amounts.get(item.item_code, 0) + abs(
			frappe.utils.flt(net_amount if net_amount is not None else item.get("amount"))
		)
	for row in si.get("taxes") or []:
		rate = frappe.utils.flt(row.get("rate"))
		amount = abs(frappe.utils.flt(row.get("tax_amount_after_discount_amount") or row.get("tax_amount")))
		if not rate or not amount:
			continue
		name = row.get("ua_prro_tax_name") or row.get("description") or row.get("account_head") or "ПДВ"
		match = re.search(r"\[([А-ЯA-Z])\]", name)
		letter = row.get("ua_prro_tax_letter") or (match.group(1) if match else ("А" if "ПДВ" in name.upper() else None))
		details = frappe.parse_json(row.get("item_wise_tax_detail") or "{}") or {}
		turnover = sum(item_amounts.get(item_code, 0) for item_code in details)
		if not turnover:
			turnover = abs(frappe.utils.flt(si.get("grand_total") or si.get("net_total")))
		result.append(
			{
				"type": int(row.get("ua_prro_tax_type") or (0 if "ПДВ" in name.upper() else 1)),
				"name": name[:64],
				"letter": letter,
				"prc": abs(rate),
				# SIGN у протоколі — податок НЕ включено у вартість, а не
				# математичний знак ставки.
				"sign": not bool(frappe.utils.cint(row.get("included_in_print_rate"))),
				"turnover": turnover,
				"sum": amount,
			}
		)
	return result


def _related_receipt(si) -> str | None:
	if not si.get("return_against"):
		return None
	return frappe.db.get_value(
		"PRRO Receipt",
		{"sales_invoice": si.return_against, "status": "Fiscalized"},
		"name",
	)


@frappe.whitelist()
def fiscalize_invoice(sales_invoice: str, client=None) -> str | None:
	"""Створює й фіскалізує чек ПРРО з POS-рахунку. Ідемпотентно."""
	existing = frappe.db.get_value(
		"PRRO Receipt",
		{"sales_invoice": sales_invoice, "status": ("in", ("Fiscalized", "Offline"))},
		"name",
	)
	if existing:
		return existing

	si = frappe.get_doc("Sales Invoice", sales_invoice)
	register = _register_for_invoice(si)
	if not register:
		frappe.throw(
			f"Для рахунку {sales_invoice} не знайдено активної каси ПРРО "
			f"(POS Profile: {si.get('pos_profile') or '—'}, "
			f"ecommerce channel: {si.get('ua_ecommerce_channel') or '—'})",
			FiscalServerError,
		)
	kep_key = _kep_key_for_invoice(si, register)
	if not kep_key:
		frappe.throw(f"Не знайдено активного ключа КЕП для касира {si.owner}", FiscalServerError)

	no_rounding_total = abs(frappe.utils.flt(si.grand_total))
	total = abs(frappe.utils.flt(si.rounded_total or si.grand_total))
	has_rounding = abs(total - no_rounding_total) > 0.001
	receipt = orch.fiscalize_sale(
		cash_register=register,
		kep_key=kep_key,
		items=_invoice_lines(si),
		payments=_invoice_payments(si),
		total=total,
		taxes=_invoice_taxes(si),
		no_rounding_total=no_rounding_total if has_rounding else None,
		rounding_sum=(no_rounding_total - total) if has_rounding else None,
		receipt_type="Повернення" if si.is_return else "Продаж",
		sales_invoice=sales_invoice,
		related_receipt=_related_receipt(si),
		pos_order=si.get("ua_pos_order"),
		idem_key=f"{'return' if si.is_return else 'sale'}:{register}:{sales_invoice}",
		client=client,
	)
	if receipt and si.get("ua_loyalty_account"):
		frappe.db.set_value(
			"PRRO Receipt",
			receipt,
			{
				"loyalty_redeemed_amount": abs(frappe.utils.flt(si.get("ua_loyalty_redeemed_amount"))),
				"loyalty_scope": si.get("ua_loyalty_scope"),
				"loyalty_snapshot_hash": si.get("ua_loyalty_snapshot_hash"),
			},
			update_modified=False,
		)
	return receipt


def on_submit(doc, method=None):
	"""Хук проведення Sales Invoice: авто-фіскалізація POS-рахунків.

	Спрацьовує лише для is_pos при увімкненій фіскалізації та наявній касі.
	Помилка не блокує проведення — чек лишається в статусі Error для повтору.
	"""
	if not doc.get("is_pos"):
		return
	# Власний UA POS фіскалізує після завершення checkout, коли доступні точні
	# дані термінала; хук лишається для стандартного ERPNext POS.
	if doc.get("ua_pos_order"):
		return
	settings = frappe.get_cached_doc("PRRO Settings")
	if not settings.enabled:
		return
	if not _register_for_invoice(doc):
		return
	try:
		fiscalize_invoice(doc.name)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"PRRO auto-fiscalize {doc.name}")
