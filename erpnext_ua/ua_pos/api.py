from __future__ import annotations

import json
import secrets
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from erpnext_ua.ua_pos.services.common import (
	SESSION_TTL,
	active_shift,
	audit,
	digest,
	get_session,
	parse_rows,
	session_key,
)
from erpnext_ua.ua_pos.terminal_service import get_adapter, resolve_terminal


FINAL_ORDER_STATUSES = {"Completed", "Completed Print Error"}
EDITABLE_ORDER_STATUSES = {"Building", "Held"}


def _access(employee: str, cash_desk: str):
	if not employee:
		return None
	today = frappe.utils.today()
	rows = frappe.db.sql(
		"""select name, access_role, valid_to from `tabEmployee Cash Desk Access`
		where employee=%s and cash_desk=%s and active=1
		and (valid_from is null or valid_from <= %s)
		and (valid_to is null or valid_to >= %s) limit 1""",
		(employee, cash_desk, today, today),
		as_dict=True,
	)
	return rows[0] if rows else None


def _owned_order(session: dict, name: str, statuses: set[str] | None = None):
	doc = frappe.get_doc("POS Order", name)
	if doc.cash_desk != session["cash_desk"]:
		frappe.throw(_("Чек належить іншій касі"), frappe.PermissionError)
	if statuses is not None and doc.status not in statuses:
		frappe.throw(_("Операція недоступна для чека у статусі {0}").format(doc.status))
	return doc


def _require_shift(session: dict) -> str:
	shift = active_shift(session["cash_desk"])
	if not shift:
		frappe.throw(_("Спочатку відкрийте управлінську зміну"))
	return shift


def _cash_balance(shift: str) -> float:
	return frappe.utils.flt(
		frappe.db.sql(
			"""select coalesce(sum(case when direction='In' then amount else -amount end), 0)
			from `tabPOS Cash Movement`
			where operational_shift=%s and docstatus=1 and is_cash_drawer=1 and currency='UAH'""",
			shift,
		)[0][0]
	)


@frappe.whitelist()
def list_cash_desks() -> list[dict]:
	"""Return active desks for the login selector without exposing secrets."""
	return frappe.get_all(
		"POS Cash Desk",
		filters={"status": "Active"},
		fields=["name", "desk_name", "company", "warehouse", "prro_cash_register"],
		order_by="desk_name asc, name asc",
		limit_page_length=200,
	)


@frappe.whitelist(allow_guest=False)
@rate_limit(key="cash_desk", limit=10, seconds=300, methods="POST", ip_based=True)
def login_by_barcode(cash_desk: str, barcode: str, device_token: str | None = None) -> dict:
	cash_desk = (cash_desk or "").strip()
	barcode = (barcode or "").strip()
	if not cash_desk or not barcode:
		frappe.throw(_("Cash desk and employee barcode are required"))
	if frappe.db.get_value("POS Cash Desk", cash_desk, "status") != "Active":
		frappe.throw("Cash desk is inactive")
	employee = frappe.db.get_value("Employee", {"ua_pos_barcode_hash": digest(barcode)}, "name")
	if not employee:
		audit("failed_access", {"cash_desk": cash_desk}, details={"device": device_token}, reason="unknown_barcode")
		frappe.throw(_("Employee barcode is not recognized"), frappe.PermissionError)
	access = _access(employee, cash_desk)
	if not access or (access.valid_to and frappe.utils.getdate(access.valid_to) < frappe.utils.getdate()):
		audit(
			"failed_access",
			{"cash_desk": cash_desk, "employee": employee},
			details={"device": device_token},
			reason="no_cash_desk_access",
		)
		frappe.throw(_("Employee {0} has no access to cash desk {1}").format(employee, cash_desk), frappe.PermissionError)
	token = secrets.token_urlsafe(32)
	session = {
		"employee": employee,
		"cash_desk": cash_desk,
		"access_role": access.access_role,
		"device_token": digest(device_token) if device_token else None,
		"created_at": frappe.utils.now(),
	}
	frappe.cache.set_value(session_key(token), json.dumps(session), expires_in_sec=SESSION_TTL)
	audit("login", session)
	return {"session_token": token, **session, "shift": active_shift(cash_desk)}


@frappe.whitelist()
def logout(pos_session_token: str):
	session = get_session(pos_session_token)
	audit("logout", session)
	frappe.cache.delete_value(session_key(pos_session_token))


@frappe.whitelist()
def session_state(pos_session_token: str) -> dict:
	session = get_session(pos_session_token)
	shift = active_shift(session["cash_desk"])
	desk = frappe.db.get_value(
		"POS Cash Desk",
		session["cash_desk"],
		["company", "warehouse", "default_customer", "terminal", "prro_cash_register", "receipt_printer"],
		as_dict=True,
	) or {}
	employee_name = frappe.db.get_value("Employee", session["employee"], "employee_name")
	unfinished = frappe.get_all(
		"POS Order",
		filters={"cash_desk": session["cash_desk"], "status": ("not in", ("Completed", "Invoice Draft", "Cancelled"))},
		fields=["name", "status", "grand_total", "modified"],
		order_by="modified desc",
		limit=10,
	)
	return {
		**session,
		"employee_name": employee_name or session["employee"],
		"shift": shift,
		"desk": desk,
		"unfinished_orders": unfinished,
	}


@frappe.whitelist()
def unfinished_orders(pos_session_token: str) -> list[dict]:
	session = get_session(pos_session_token)
	return frappe.get_all(
		"POS Order",
		filters={
			"cash_desk": session["cash_desk"],
			"operational_shift": _require_shift(session),
			"status": ("in", ("Building", "Held")),
		},
		fields=["name", "status", "customer", "grand_total", "modified"],
		order_by="modified desc",
		limit=10,
	)


@frappe.whitelist()
def stock_search(pos_session_token: str, query: str, limit: int = 30) -> list[dict]:
	session = get_session(pos_session_token)
	desk = frappe.get_doc("POS Cash Desk", session["cash_desk"])
	query = (query or "").strip()
	if len(query) < 2:
		frappe.throw(_("Введіть щонайменше два символи для пошуку"))
	like = f"%{query}%"
	rows = frappe.db.sql(
		"""select i.name as item_code, i.item_name, i.stock_uom as uom,
			i.image, coalesce(b.actual_qty, 0) as actual_qty,
			(select ib.barcode from `tabItem Barcode` ib where ib.parent=i.name order by ib.idx limit 1) as barcode
		from `tabItem` i
		left join `tabBin` b on b.item_code=i.name and b.warehouse=%s
		where i.disabled=0 and (i.name like %s or i.item_name like %s or exists(
			select 1 from `tabItem Barcode` ib2 where ib2.parent=i.name and ib2.barcode like %s
		))
		order by case when i.name=%s then 0 else 1 end, i.item_name
		limit %s""",
		(desk.warehouse, like, like, like, query, min(max(int(limit or 30), 1), 100)),
		as_dict=True,
	)
	for row in rows:
		row["rate"] = frappe.utils.flt(
			frappe.db.get_value(
				"Item Price",
				{"item_code": row.item_code, "selling": 1},
				"price_list_rate",
				order_by="valid_from desc, modified desc",
			)
			or 0
		)
	return rows


@frappe.whitelist()
def cash_operation(
	pos_session_token: str,
	movement_type: str,
	amount: float,
	idem_key: str,
	notes: str = "",
) -> dict:
	session = get_session(pos_session_token)
	shift = _require_shift(session)
	allowed = {
		"Cash In": "In",
		"Expense": "Out",
		"Incassation Out": "Out",
	}
	if movement_type not in allowed:
		frappe.throw(_("Непідтримувана касова операція"))
	if movement_type in {"Expense", "Incassation Out"} and session["access_role"] not in {"Senior Cashier", "Manager"}:
		frappe.throw(_("Ця касова операція потребує ролі старшого касира або менеджера"), frappe.PermissionError)
	amount = frappe.utils.flt(amount, 2)
	if amount <= 0:
		frappe.throw(_("Сума має бути більшою за нуль"))
	existing = frappe.db.get_value("POS Cash Movement", {"idem_key": idem_key}, "name")
	if existing:
		return frappe.get_doc("POS Cash Movement", existing).as_dict()
	if allowed[movement_type] == "Out" and amount > _cash_balance(shift) + 0.001:
		frappe.throw(_("У касі недостатньо готівки для цієї операції"))
	doc = frappe.get_doc(
		{
			"doctype": "POS Cash Movement",
			"cash_desk": session["cash_desk"],
			"operational_shift": shift,
			"employee": session["employee"],
			"direction": allowed[movement_type],
			"movement_type": movement_type,
			"amount": amount,
			"currency": "UAH",
			"is_cash_drawer": 1,
			"idem_key": idem_key,
			"notes": (notes or "").strip(),
		}
	).insert(ignore_permissions=True)
	doc.submit()
	desk = frappe.get_doc("POS Cash Desk", session["cash_desk"])
	if desk.prro_cash_register:
		register = frappe.get_doc("PRRO Cash Register", desk.prro_cash_register)
		if register.current_shift:
			from erpnext_ua.ua_fiscal import orchestration

			key = desk.default_kep_key or register.default_kep_key
			try:
				receipt = orchestration.fiscalize_service_cash(
					register.name,
					key,
					amount,
					allowed[movement_type],
					f"cash-movement:{doc.name}",
				)
				status = frappe.db.get_value("PRRO Receipt", receipt, "status")
				doc.db_set("prro_receipt", receipt, update_modified=False)
				doc.db_set("fiscal_status", "Offline" if status == "Offline" else "Fiscalized", update_modified=False)
			except Exception as exc:
				doc.db_set("fiscal_status", "Pending", update_modified=False)
				frappe.log_error(frappe.get_traceback(), f"PRRO service cash {doc.name}")
				doc.notes = f"{doc.notes or ''}\nПРРО pending: {str(exc)[:200]}".strip()
				doc.db_set("notes", doc.notes, update_modified=False)
	audit("cash_operation", session, (doc.doctype, doc.name), {"type": movement_type, "amount": amount})
	frappe.db.commit()
	return {**doc.as_dict(), "cash_balance": _cash_balance(shift)}


@frappe.whitelist()
def shift_report(pos_session_token: str) -> dict:
	session = get_session(pos_session_token)
	shift = _require_shift(session)
	shift_doc = frappe.get_doc("POS Operational Shift", shift)
	orders = frappe.get_all(
		"POS Order",
		filters={"operational_shift": shift, "status": ("in", tuple(FINAL_ORDER_STATUSES))},
		fields=["name", "order_type", "grand_total", "sales_invoice", "customer", "modified"],
		order_by="creation",
	)
	order_names = [row.name for row in orders]
	movements = frappe.get_all(
		"POS Cash Movement",
		filters={"operational_shift": shift, "docstatus": 1},
		fields=["name", "direction", "movement_type", "amount", "currency", "notes", "creation"],
		order_by="creation",
	)
	payment_totals = []
	item_totals = []
	if order_names:
		order_names = tuple(order_names)
		payment_totals = frappe.db.sql(
			"""select p.kind, p.mode_of_payment, sum(p.amount) as amount
			from `tabPOS Order Payment` p
			where p.parent in %(orders)s and p.status='Confirmed'
			group by p.kind, p.mode_of_payment order by p.kind, p.mode_of_payment""",
			{"orders": order_names},
			as_dict=True,
		)
		item_totals = frappe.db.sql(
			"""select i.item_code, max(i.item_name) as item_name,
				sum(case when o.order_type='Return' then -i.qty else i.qty end) as qty,
				sum(case when o.order_type='Return' then -i.amount else i.amount end) as amount
			from `tabPOS Order Item` i join `tabPOS Order` o on o.name=i.parent
			where i.parent in %(orders)s group by i.item_code order by item_name""",
			{"orders": order_names},
			as_dict=True,
		)
	sales_total = sum(frappe.utils.flt(row.grand_total) for row in orders if row.order_type != "Return")
	returns_total = sum(frappe.utils.flt(row.grand_total) for row in orders if row.order_type == "Return")
	return {
		"shift": shift_doc.as_dict(),
		"orders": orders,
		"movements": movements,
		"payment_totals": payment_totals,
		"item_totals": item_totals,
		"sales_total": sales_total,
		"returns_total": returns_total,
		"net_sales": sales_total - returns_total,
		"cash_balance": _cash_balance(shift),
	}


@frappe.whitelist()
def fiscal_status(pos_session_token: str) -> dict:
	session = get_session(pos_session_token)
	desk = frappe.get_doc("POS Cash Desk", session["cash_desk"])
	if not desk.prro_cash_register:
		return {"configured": False, "message": _("Для каси не налаштовано ПРРО")}
	register = frappe.get_doc("PRRO Cash Register", desk.prro_cash_register)
	shift = frappe.get_doc("PRRO Shift", register.current_shift).as_dict() if register.current_shift else None
	last_shift_name = frappe.db.get_value(
		"PRRO Shift",
		{"cash_register": register.name},
		"name",
		order_by="creation desc",
	)
	last_shift = None
	if last_shift_name:
		last_shift = frappe.db.get_value(
			"PRRO Shift",
			last_shift_name,
			["name", "status", "opened_at", "closed_at", "z_report_fiscal_number"],
			as_dict=True,
		)
	return {"configured": True, "register": register.name, "current_shift": shift, "last_shift": last_shift}


def _fiscal_receipt_for_report(shift_name: str, receipt_kind: str):
	name = frappe.db.get_value(
		"PRRO Receipt",
		{
			"shift": shift_name,
			"receipt_kind": receipt_kind,
			"status": ("in", ("Fiscalized", "Offline")),
		},
		"name",
		order_by="local_number desc",
	)
	return frappe.get_doc("PRRO Receipt", name) if name else None


def _fiscal_xml_head(receipt) -> dict:
	if not receipt or not receipt.receipt_xml:
		return {}
	try:
		root = ET.fromstring(receipt.receipt_xml.encode("windows-1251"))
	except (UnicodeEncodeError, ET.ParseError):
		return {}
	head = root.find("CHECKHEAD")
	if head is None:
		head = root.find("ZREPHEAD")
	if head is None:
		return {}
	return {child.tag: child.text or "" for child in head}


def _fiscal_report_data(cash_desk: str, report_type: str, shift_name: str | None = None) -> dict:
	"""Builds a printable snapshot from the immutable PRRO ledger."""
	kind = str(report_type or "").strip().upper()
	if kind not in {"OPENING", "X", "Z"}:
		frappe.throw(_("Невідомий тип звіту ПРРО"))
	desk = frappe.get_doc("POS Cash Desk", cash_desk)
	if not desk.prro_cash_register:
		frappe.throw(_("Для каси не налаштовано ПРРО"))
	register = frappe.get_doc("PRRO Cash Register", desk.prro_cash_register)
	shift_name = shift_name or register.current_shift
	if not shift_name and kind in {"OPENING", "Z"}:
		shift_name = frappe.db.get_value(
			"PRRO Shift",
			{"cash_register": register.name},
			"name",
			order_by="creation desc",
		)
	if not shift_name:
		frappe.throw(_("Фіскальну зміну не знайдено"))
	shift = frappe.get_doc("PRRO Shift", shift_name)
	if shift.cash_register != register.name:
		frappe.throw(_("Зміна належить іншій касі ПРРО"), frappe.PermissionError)

	from erpnext_ua.ua_fiscal import orchestration

	opening = _fiscal_receipt_for_report(shift.name, "Open Shift")
	if not opening:
		frappe.throw(_("Підтверджений документ відкриття зміни не знайдено"))
	z_receipt = _fiscal_receipt_for_report(shift.name, "Z Report")
	if kind == "Z" and not z_receipt:
		frappe.throw(_("Підтверджений Z-звіт ще не сформовано"))
	totals = orchestration._shift_totals(shift.name)
	head = _fiscal_xml_head(z_receipt if kind == "Z" else opening)

	def payment_total(bucket: dict, code: int) -> float:
		return frappe.utils.flt(
			sum(frappe.utils.flt(row.get("sum")) for row in bucket.get("payforms", []) if int(row.get("code") or 0) == code),
			2,
		)

	sales = frappe.utils.flt(totals["realiz"]["sum"], 2)
	returns = frappe.utils.flt(totals["returns"]["sum"], 2)
	cash_balance = frappe.utils.flt(
		payment_total(totals["realiz"], 0)
		- payment_total(totals["returns"], 0)
		+ totals["service_input"]
		- totals["service_output"],
		2,
	)
	document = opening if kind == "OPENING" else z_receipt if kind == "Z" else None
	titles = {
		"OPENING": "ЧЕК ВІДКРИТТЯ ЗМІНИ",
		"X": "X-ЗВІТ",
		"Z": "Z-ЗВІТ",
	}
	return {
		"report_type": kind,
		"title": titles[kind],
		"non_fiscal": kind == "X",
		"generated_at": str(frappe.utils.now_datetime()),
		"organization": head.get("ORGNM") or frappe.db.get_value("FOP Profile", register.fop_profile, "prro_registered_name"),
		"tax_id": head.get("TIN") or frappe.db.get_value("FOP Profile", register.fop_profile, "tax_id"),
		"point_name": head.get("POINTNM") or register.unit_name,
		"point_address": head.get("POINTADDR") or register.unit_address,
		"cashier": head.get("CASHIER") or shift.cashier,
		"cash_register": register.name,
		"cash_register_fiscal_number": register.fiscal_number,
		"cash_desk_local_number": register.register_local_number,
		"shift": shift.name,
		"operational_shift": shift.operational_shift,
		"shift_status": shift.status,
		"opened_at": str(shift.opened_at or ""),
		"closed_at": str(shift.closed_at or ""),
		"document_name": document.name if document else None,
		"local_number": document.local_number if document else None,
		"fiscal_number": document.fiscal_number if document else None,
		"is_offline": int(document.is_offline or 0) if document else 0,
		"testing": str(head.get("TESTING") or "").lower() == "true",
		"receipts_count": int(totals["realiz"]["count"] + totals["returns"]["count"]),
		"sales_total": sales,
		"returns_total": returns,
		"net_total": frappe.utils.flt(sales - returns, 2),
		"service_input": frappe.utils.flt(totals["service_input"], 2),
		"service_output": frappe.utils.flt(totals["service_output"], 2),
		"cash_balance": cash_balance,
		"sales_payforms": totals["realiz"]["payforms"],
		"return_payforms": totals["returns"]["payforms"],
		"sales_taxes": totals["realiz"]["taxes"],
		"return_taxes": totals["returns"]["taxes"],
	}


@frappe.whitelist()
def fiscal_report_data(pos_session_token: str, report_type: str, shift: str | None = None) -> dict:
	session = get_session(pos_session_token)
	return _fiscal_report_data(session["cash_desk"], report_type, shift)


@frappe.whitelist()
def queue_fiscal_report_print(
	pos_session_token: str,
	report_type: str,
	shift: str | None = None,
	idem_key: str | None = None,
) -> dict:
	session = get_session(pos_session_token)
	report = _fiscal_report_data(session["cash_desk"], report_type, shift)
	desk = frappe.get_doc("POS Cash Desk", session["cash_desk"])
	if not desk.receipt_printer:
		return {"fallback_browser": True, "report": report}
	from erpnext_ua.ua_pos.print_service import queue_fiscal_report

	job = queue_fiscal_report(
		desk.name,
		report,
		idem_key=idem_key or uuid.uuid4().hex,
	)
	audit("fiscal_report_print_queued", session, ("PRRO Shift", report["shift"]), {"print_job": job.name})
	return {"fallback_browser": False, "job": job.name, "status": job.status}


@frappe.whitelist()
def fiscal_open_shift(pos_session_token: str) -> dict:
	session = get_session(pos_session_token)
	operational_shift = _require_shift(session)
	desk = frappe.get_doc("POS Cash Desk", session["cash_desk"])
	if not desk.prro_cash_register:
		frappe.throw(_("Для каси не налаштовано ПРРО"))
	from erpnext_ua.ua_fiscal import orchestration

	register = frappe.get_doc("PRRO Cash Register", desk.prro_cash_register)
	if register.current_shift:
		# Legacy/recovered openings could miss this relation even though the
		# management shift was validated before the fiscal operation.
		if not frappe.db.get_value("PRRO Shift", register.current_shift, "operational_shift"):
			frappe.db.set_value(
				"PRRO Shift",
				register.current_shift,
				"operational_shift",
				operational_shift,
				update_modified=False,
			)
			frappe.db.commit()
		return fiscal_status(pos_session_token)
	key = desk.default_kep_key or register.default_kep_key
	if not key:
		frappe.throw(_("Для ПРРО не налаштовано КЕП"))
	try:
		orchestration.open_shift(
			register.name,
			key,
			operational_shift=operational_shift,
		)
	except Exception:
		# Після остаточно відхиленого документа recovery має одразу звірити
		# його відсутність у ДПС і повернути локальний номер. Інакше перша
		# помилка приховує причину, а друга спроба лише повідомляє про
		# незавершений ledger-запис.
		failed_receipt = frappe.db.get_value(
			"PRRO Receipt",
			{
				"cash_register": register.name,
				"receipt_kind": "Open Shift",
				"status": ("in", ("Uncertain", "Error", "Fiscalized")),
			},
			"name",
			order_by="local_number desc",
		)
		recovered = None
		if failed_receipt:
			try:
				recovered = orchestration.reconcile_receipt(failed_receipt)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"PRRO open-shift recovery {failed_receipt}",
				)
		# Timeout/protocol ambiguity can coexist with an accepted DPS document.
		# If immediate reconciliation confirmed it, return success instead of
		# asking the cashier to click again and risking a duplicate operation.
		if not recovered or recovered.get("status") != "Fiscalized":
			raise
	audit("fiscal_shift_open", session, ("PRRO Cash Register", register.name))
	return fiscal_status(pos_session_token)


@frappe.whitelist()
def fiscal_close_shift(pos_session_token: str) -> dict:
	session = get_session(pos_session_token)
	if session["access_role"] not in {"Senior Cashier", "Manager"}:
		frappe.throw(_("Закриття фіскальної зміни потребує старшого касира або менеджера"), frappe.PermissionError)
	desk = frappe.get_doc("POS Cash Desk", session["cash_desk"])
	if not desk.prro_cash_register:
		frappe.throw(_("Для каси не налаштовано ПРРО"))
	from erpnext_ua.ua_fiscal import orchestration

	register = frappe.get_doc("PRRO Cash Register", desk.prro_cash_register)
	if not register.current_shift:
		return fiscal_status(pos_session_token)
	key = desk.default_kep_key or register.default_kep_key
	if not key:
		frappe.throw(_("Для ПРРО не налаштовано КЕП"))
	try:
		orchestration.close_shift(register.name, key)
	except Exception:
		failed_name = frappe.db.get_value(
			"PRRO Receipt",
			{
				"cash_register": register.name,
				"shift": register.current_shift,
				"receipt_kind": ("in", ("Z Report", "Close Shift")),
				"status": ("in", ("Uncertain", "Error", "Fiscalized")),
			},
			"name",
			order_by="local_number desc",
		)
		recovered = None
		if failed_name:
			try:
				recovered = orchestration.reconcile_receipt(failed_name)
			except Exception:
				frappe.log_error(frappe.get_traceback(), f"PRRO close-shift recovery {failed_name}")
		if not recovered or recovered.get("status") != "Fiscalized":
			raise
		# If only Z was accepted, continue with the distinct Close Shift
		# document. close_shift resumes the confirmed Z idempotently.
		if recovered.get("receipt_kind") == "Z Report":
			orchestration.close_shift(register.name, key)
	audit("fiscal_shift_close", session, ("PRRO Cash Register", register.name))
	return fiscal_status(pos_session_token)


def _count_total(rows: list[dict]) -> float:
	return sum(frappe.utils.flt(row.get("denomination")) * int(row.get("qty") or 0) for row in rows)


@frappe.whitelist()
def open_shift(pos_session_token: str, denominations, idem_key: str) -> dict:
	session = get_session(pos_session_token)
	existing = frappe.db.get_value("POS Operational Shift", {"idem_key": idem_key}, "name")
	if existing:
		return frappe.get_doc("POS Operational Shift", existing).as_dict()
	rows = parse_rows(denominations)
	frappe.db.sql("select name from `tabPOS Cash Desk` where name=%s for update", session["cash_desk"])
	if active_shift(session["cash_desk"], for_update=True):
		frappe.throw("An operational shift is already open on this cash desk")
	doc = frappe.get_doc(
		{
			"doctype": "POS Operational Shift",
			"cash_desk": session["cash_desk"],
			"responsible_employee": session["employee"],
			"status": "Open",
			"opened_by": frappe.session.user,
			"opened_at": frappe.utils.now_datetime(),
			"idem_key": idem_key,
			"opening_counts": [{**row, "context": "Opening"} for row in rows],
		}
	).insert(ignore_permissions=True)
	for currency in {row.get("currency") or "UAH" for row in rows}:
		amount = _count_total([row for row in rows if (row.get("currency") or "UAH") == currency])
		if amount:
			frappe.get_doc(
				{
					"doctype": "POS Cash Movement",
					"cash_desk": session["cash_desk"],
					"operational_shift": doc.name,
					"employee": session["employee"],
					"direction": "In",
					"movement_type": "Opening Float",
					"amount": amount,
					"currency": currency,
					"is_cash_drawer": 1,
					"basis_doctype": "POS Operational Shift",
					"basis_name": doc.name,
				}
			).insert(ignore_permissions=True).submit()
	audit("shift_open", session, (doc.doctype, doc.name), {"opening": _count_total(rows)})
	frappe.db.commit()
	return doc.as_dict()


def _expected_cash(shift: str) -> float:
	return frappe.utils.flt(
		frappe.db.sql(
			"""select coalesce(sum(case when direction='In' then amount else -amount end), 0)
			from `tabPOS Cash Movement` where operational_shift=%s and docstatus=1 and is_cash_drawer=1""",
			shift,
		)[0][0]
	)


@frappe.whitelist()
def close_shift_begin(pos_session_token: str) -> dict:
	session = get_session(pos_session_token)
	shift = active_shift(session["cash_desk"])
	if not shift:
		frappe.throw("No open shift")
	blocking = frappe.get_all(
		"POS Order",
		filters={"operational_shift": shift, "status": ("not in", ("Completed", "Invoice Draft", "Cancelled"))},
		pluck="name",
	)
	return {"shift": shift, "expected": _expected_cash(shift), "blocking_orders": blocking}


@frappe.whitelist()
def close_shift_confirm(pos_session_token: str, denominations, idem_key: str, comment: str = "") -> dict:
	session = get_session(pos_session_token)
	existing = frappe.db.get_value("POS Operational Shift", {"close_idem_key": idem_key}, "name")
	if existing:
		return frappe.get_doc("POS Operational Shift", existing).as_dict()
	rows = parse_rows(denominations)
	shift_name = active_shift(session["cash_desk"], for_update=True)
	if not shift_name:
		frappe.throw("No open shift")
	if frappe.db.exists("POS Order", {"operational_shift": shift_name, "status": ("not in", ("Completed", "Invoice Draft", "Cancelled"))}):
		frappe.throw("Resolve unfinished POS orders before closing the shift")
	doc = frappe.get_doc("POS Operational Shift", shift_name)
	expected, counted = _expected_cash(shift_name), _count_total(rows)
	discrepancy = counted - expected
	if discrepancy and not comment.strip():
		frappe.throw("A cashier comment is required when cash differs from expected")
	if discrepancy and session["access_role"] not in {"Senior Cashier", "Manager"}:
		frappe.throw(_("Зміну з розбіжністю має закрити старший касир або менеджер"), frappe.PermissionError)
	doc.status = "Closed"
	doc.set("closing_counts", [{**row, "context": "Closing"} for row in rows])
	doc.expected_cash = expected
	doc.counted_cash = counted
	doc.discrepancy = discrepancy
	doc.closing_comment = comment
	doc.close_idem_key = idem_key
	doc.closed_by = frappe.session.user
	doc.closed_at = frappe.utils.now_datetime()
	doc.save(ignore_permissions=True)
	audit("shift_close", session, (doc.doctype, doc.name), {"expected": expected, "counted": counted})
	frappe.db.commit()
	return doc.as_dict()


@frappe.whitelist()
def create_order(pos_session_token: str, idem_key: str, customer: str | None = None, fiscal_mode="Fiscal") -> dict:
	session = get_session(pos_session_token)
	existing = frappe.db.get_value("POS Order", {"idem_key": idem_key}, "name")
	if existing:
		return frappe.get_doc("POS Order", existing).as_dict()
	shift = active_shift(session["cash_desk"])
	if not shift:
		frappe.throw("Open a shift before creating an order")
	desk = frappe.get_doc("POS Cash Desk", session["cash_desk"])
	doc = frappe.get_doc(
		{
			"doctype": "POS Order",
			"cash_desk": desk.name,
			"operational_shift": shift,
			"employee": session["employee"],
			"customer": customer or desk.default_customer,
			"fiscal_mode": fiscal_mode,
			"lookup_token": str(uuid.uuid4()),
			"idem_key": idem_key,
		}
	).insert(ignore_permissions=True)
	return doc.as_dict()


def _resolve_item(query: str) -> tuple[str, str | None]:
	barcode = frappe.db.get_value("Item Barcode", {"barcode": query}, ["parent", "barcode"], as_dict=True)
	if barcode:
		return barcode.parent, barcode.barcode
	if frappe.db.exists("Item", query):
		return query, None
	rows = frappe.get_all("Item", filters={"item_name": ("like", f"%{query}%"), "disabled": 0}, pluck="name", limit=2)
	if len(rows) != 1:
		frappe.throw("Item not found or query is ambiguous")
	return rows[0], None


@frappe.whitelist()
def scan_item(pos_session_token: str, order: str, query: str, qty: float = 1) -> dict:
	session = get_session(pos_session_token)
	doc = _owned_order(session, order, {"Building"})
	qty = frappe.utils.flt(qty)
	if qty <= 0:
		frappe.throw(_("Кількість має бути більшою за нуль"))
	item_code, barcode = _resolve_item(query.strip())
	item = frappe.db.get_value(
		"Item",
		item_code,
		["item_name", "image", "stock_uom", "disabled", "is_stock_item", "has_batch_no", "has_serial_no"],
		as_dict=True,
	)
	if item.disabled:
		frappe.throw(_("Товар вимкнено"))
	desk = frappe.get_doc("POS Cash Desk", doc.cash_desk)
	rate = frappe.db.get_value("Item Price", {"item_code": item_code, "selling": 1}, "price_list_rate") or 0
	if frappe.utils.flt(rate) <= 0:
		frappe.throw(_("Для товару {0} не задано ціну продажу").format(item_code))
	for row in doc.items:
		if row.item_code == item_code and not row.serial_no and not row.batch_no:
			row.qty += qty
			if int(doc.birthday_benefit_year or 0):
				_allocate_order_discount(doc, discount_percent=doc.birthday_discount_percent)
			doc.save(ignore_permissions=True)
			return doc.as_dict()
	doc.append(
		"items",
		{
			"item_code": item_code,
			"item_name": item.item_name,
			"image": item.image,
			"barcode": barcode,
			"qty": qty,
			"uom": item.stock_uom,
			"rate": rate,
			"warehouse": desk.warehouse,
		},
	)
	if int(doc.birthday_benefit_year or 0):
		_allocate_order_discount(doc, discount_percent=doc.birthday_discount_percent)
	doc.save(ignore_permissions=True)
	return doc.as_dict()


def _birthday_occurrence(birth_date, year: int) -> date:
	birth_date = frappe.utils.getdate(birth_date)
	try:
		return birth_date.replace(year=year)
	except ValueError:
		return date(year, 2, 28)


def _birthday_window(birth_date, days_before: int, days_after: int, today=None):
	today = frappe.utils.getdate(today or frappe.utils.today())
	for year in (today.year - 1, today.year, today.year + 1):
		birthday = _birthday_occurrence(birth_date, year)
		valid_from = frappe.utils.add_days(birthday, -max(0, int(days_before or 0)))
		valid_until = frappe.utils.add_days(birthday, max(0, int(days_after or 0)))
		if frappe.utils.getdate(valid_from) <= today <= frappe.utils.getdate(valid_until):
			return {
				"benefit_year": year,
				"birthday": birthday,
				"valid_from": frappe.utils.getdate(valid_from),
				"valid_until": frappe.utils.getdate(valid_until),
				"age": year - frappe.utils.getdate(birth_date).year,
			}
	return None


@frappe.whitelist()
def birthday_offer(pos_session_token: str, customer: str, order: str | None = None) -> dict:
	session = get_session(pos_session_token)
	settings = frappe.get_cached_doc("POS Birthday Settings")
	if not settings.enabled:
		return {"eligible": False, "reason": _("Політику дня народження вимкнено")}
	if not frappe.db.exists("Customer", customer):
		return {"eligible": False, "reason": _("Покупця не знайдено")}
	row = frappe.db.get_value(
		"Customer",
		customer,
		["name", "customer_name", "customer_group", "ua_first_name", "ua_date_of_birth"],
		as_dict=True,
	)
	if not row.ua_date_of_birth:
		return {"eligible": False, "reason": _("Для покупця не вказано дату народження")}
	if settings.eligible_customer_group and row.customer_group != settings.eligible_customer_group:
		return {"eligible": False, "reason": _("Група покупця не бере участі у програмі")}
	window = _birthday_window(row.ua_date_of_birth, settings.days_before, settings.days_after)
	if not window:
		return {"eligible": False, "reason": _("Сьогодні знижка до дня народження не діє")}
	if int(settings.min_age or 0) and window["age"] < int(settings.min_age):
		return {"eligible": False, "reason": _("Вік покупця менший за дозволений")}
	if int(settings.max_age or 0) and window["age"] > int(settings.max_age):
		return {"eligible": False, "reason": _("Вік покупця більший за дозволений")}
	usage_key = f"{customer}:{window['benefit_year']}"
	if settings.one_time_per_year and frappe.db.exists("POS Birthday Benefit", {"usage_key": usage_key}):
		return {"eligible": False, "reason": _("Знижку цього року вже використано")}
	order_total = 0
	applied = False
	if order:
		doc = _owned_order(session, order)
		if doc.customer != customer:
			return {"eligible": False, "reason": _("Покупець не відповідає поточному чеку")}
		order_total = frappe.utils.flt(doc.grand_total)
		applied = int(doc.birthday_benefit_year or 0) == window["benefit_year"]
	minimum = frappe.utils.flt(settings.minimum_order_amount)
	return {
		"eligible": bool(applied or order_total >= minimum),
		"minimum_met": bool(order_total >= minimum),
		"minimum_order_amount": minimum,
		"customer": row,
		"discount_percent": frappe.utils.flt(settings.discount_percent),
		"birthday": str(window["birthday"]),
		"valid_from": str(window["valid_from"]),
		"valid_until": str(window["valid_until"]),
		"benefit_year": window["benefit_year"],
		"age": window["age"],
		"applied": applied,
		"usage_key": usage_key,
	}


@frappe.whitelist()
def apply_birthday_discount(pos_session_token: str, order: str) -> dict:
	session = get_session(pos_session_token)
	doc = _owned_order(session, order, {"Building"})
	offer = birthday_offer(pos_session_token, doc.customer, doc.name)
	if not offer.get("eligible"):
		frappe.throw(offer.get("reason") or _("Знижка до дня народження недоступна"))
	discounted = set_order_discount(
		pos_session_token,
		doc.name,
		discount_percent=offer["discount_percent"],
	)
	doc = frappe.get_doc("POS Order", discounted.name)
	doc.birthday_benefit_year = offer["benefit_year"]
	doc.birthday_discount_percent = offer["discount_percent"]
	doc.save(ignore_permissions=True)
	audit("birthday_discount_applied", session, (doc.doctype, doc.name), offer)
	return doc.as_dict()


@frappe.whitelist()
def set_item_qty(pos_session_token: str, order: str, row_name: str, qty: float) -> dict:
	session = get_session(pos_session_token)
	doc = _owned_order(session, order, {"Building"})
	row = next((row for row in doc.items if row.name == row_name), None)
	if not row:
		frappe.throw("Order item not found")
	if frappe.utils.flt(qty) <= 0:
		doc.remove(row)
	else:
		row.qty = frappe.utils.flt(qty)
	if int(doc.birthday_benefit_year or 0):
		_allocate_order_discount(doc, discount_percent=doc.birthday_discount_percent)
	doc.save(ignore_permissions=True)
	return doc.as_dict()


@frappe.whitelist()
def get_order(pos_session_token: str, order: str) -> dict:
	session = get_session(pos_session_token)
	doc = frappe.get_doc("POS Order", order)
	if doc.cash_desk != session["cash_desk"]:
		frappe.throw("Order belongs to another cash desk", frappe.PermissionError)
	return doc.as_dict()


@frappe.whitelist()
def set_order_customer(pos_session_token: str, order: str, customer: str) -> dict:
	session = get_session(pos_session_token)
	doc = _owned_order(session, order, {"Building"})
	if not frappe.db.exists("Customer", customer):
		frappe.throw(_("Customer {0} does not exist").format(customer))
	if doc.customer != customer and int(doc.birthday_benefit_year or 0):
		_allocate_order_discount(doc)
		doc.birthday_benefit_year = 0
		doc.birthday_discount_percent = 0
	doc.customer = customer
	doc.save(ignore_permissions=True)
	return doc.as_dict()


@frappe.whitelist()
def set_order_mode(pos_session_token: str, order: str, fiscal_mode: str) -> dict:
	session = get_session(pos_session_token)
	doc = _owned_order(session, order, {"Building"})
	if fiscal_mode not in {"Fiscal", "Non Fiscal"}:
		frappe.throw(_("Unsupported fiscal mode"))
	if fiscal_mode == "Non Fiscal" and session["access_role"] not in {"Senior Cashier", "Manager"}:
		frappe.throw(_("Нефіскальний режим потребує дозволу старшого касира або менеджера"), frappe.PermissionError)
	doc.fiscal_mode = fiscal_mode
	doc.save(ignore_permissions=True)
	audit("fiscal_mode_change", session, (doc.doctype, doc.name), {"mode": fiscal_mode})
	return doc.as_dict()


def _allocate_order_discount(doc, discount_percent: float = 0, discount_amount: float = 0):
	percent = frappe.utils.flt(discount_percent)
	amount = frappe.utils.flt(discount_amount, 2)
	if percent < 0 or percent > 100 or amount < 0:
		frappe.throw(_("Некоректна знижка"))
	gross_total = sum(frappe.utils.flt(row.qty) * frappe.utils.flt(row.rate) for row in doc.items)
	target = gross_total * percent / 100 if percent else amount
	target = min(gross_total, target)
	allocated = 0.0
	for idx, row in enumerate(doc.items):
		gross = frappe.utils.flt(row.qty) * frappe.utils.flt(row.rate)
		row.discount_amount = (
			frappe.utils.flt(target - allocated, 2)
			if idx == len(doc.items) - 1
			else frappe.utils.flt(target * gross / gross_total, 2)
		)
		allocated += row.discount_amount
	return target


@frappe.whitelist()
def set_order_discount(
	pos_session_token: str,
	order: str,
	discount_percent: float = 0,
	discount_amount: float = 0,
) -> dict:
	session = get_session(pos_session_token)
	doc = _owned_order(session, order, {"Building"})
	if session["access_role"] not in {"Senior Cashier", "Manager"}:
		frappe.throw(_("Ручна знижка потребує дозволу старшого касира або менеджера"), frappe.PermissionError)
	if not doc.items:
		frappe.throw(_("У чеку немає товарів"))
	target = _allocate_order_discount(doc, discount_percent, discount_amount)
	doc.birthday_benefit_year = 0
	doc.birthday_discount_percent = 0
	doc.save(ignore_permissions=True)
	audit("order_discount", session, (doc.doctype, doc.name), {"amount": target, "percent": frappe.utils.flt(discount_percent)})
	return doc.as_dict()


@frappe.whitelist()
def set_item_tracking(
	pos_session_token: str,
	order: str,
	row_name: str,
	batch_no: str | None = None,
	serial_no: str | None = None,
) -> dict:
	session = get_session(pos_session_token)
	doc = _owned_order(session, order, {"Building"})
	row = next((item for item in doc.items if item.name == row_name), None)
	if not row:
		frappe.throw(_("Рядок товару не знайдено"))
	row.batch_no = (batch_no or "").strip() or None
	row.serial_no = (serial_no or "").strip() or None
	doc.save(ignore_permissions=True)
	return doc.as_dict()


@frappe.whitelist()
def hold_order(pos_session_token: str, order: str) -> dict:
	session = get_session(pos_session_token)
	doc = _owned_order(session, order, EDITABLE_ORDER_STATUSES)
	if doc.status == "Building":
		held_count = frappe.db.count(
			"POS Order",
			{"cash_desk": session["cash_desk"], "operational_shift": doc.operational_shift, "status": "Held"},
		)
		if held_count >= 5:
			frappe.throw(_("На касі вже є п’ять відкладених чеків"))
	doc.status = "Held" if doc.status == "Building" else "Building"
	doc.save(ignore_permissions=True)
	audit("order_held" if doc.status == "Held" else "order_resumed", session, (doc.doctype, doc.name))
	return doc.as_dict()


@frappe.whitelist()
def cancel_order(pos_session_token: str, order: str) -> dict:
	session = get_session(pos_session_token)
	doc = frappe.get_doc("POS Order", order)
	if doc.cash_desk != session["cash_desk"] or doc.status not in {"Building", "Held"}:
		frappe.throw(_("Only an unpaid cart can be cancelled"), frappe.PermissionError)
	doc.status = "Cancelled"
	doc.save(ignore_permissions=True)
	audit("order_cancelled", session, (doc.doctype, doc.name))
	return doc.as_dict()


def _attempt(order, payment: dict, number: int, idem_key: str):
	existing = frappe.db.get_value("POS Payment Attempt", {"idem_key": f"{idem_key}:{number}"}, "name")
	if existing:
		return frappe.get_doc("POS Payment Attempt", existing)
	attempt = frappe.get_doc(
		{
			"doctype": "POS Payment Attempt",
			"pos_order": order.name,
			"attempt_no": number,
			"mode_of_payment": payment["mode_of_payment"],
			"kind": payment["kind"],
			"amount": payment["amount"],
			"currency": payment.get("currency") or "UAH",
			"idem_key": f"{idem_key}:{number}",
		}
	).insert(ignore_permissions=True)
	return attempt


def _masked_terminal_payload(value):
	if isinstance(value, dict):
		result = {}
		for key, item in value.items():
			normalized = str(key).lower().replace("_", "")
			if any(token in normalized for token in ("password", "secret", "apikey", "track", "cvv", "pin")):
				result[key] = "***"
			elif normalized in {"pan", "cardnumber", "cardno"}:
				text = str(item or "")
				result[key] = f"****{text[-4:]}" if text else "***"
			else:
				result[key] = _masked_terminal_payload(item)
		return result
	if isinstance(value, list):
		return [_masked_terminal_payload(item) for item in value]
	return value


def _ensure_terminal_transaction(attempt, terminal: str, operation_id: str, operation: str):
	existing = frappe.db.get_value("Terminal Transaction", {"operation_id": operation_id}, "name")
	if existing:
		txn = frappe.get_doc("Terminal Transaction", existing)
	else:
		txn = frappe.get_doc(
			{
				"doctype": "Terminal Transaction",
				"payment_attempt": attempt.name,
				"terminal": terminal,
				"operation": operation,
				"operation_id": operation_id,
				"amount": attempt.amount,
				"currency": attempt.currency,
				"status": "Unknown",
				"request_json": frappe.as_json({"operation_id": operation_id, "amount": attempt.amount}),
			}
		).insert(ignore_permissions=True)
	attempt.db_set("terminal_transaction", txn.name, update_modified=False)
	attempt.terminal_transaction = txn.name
	# External charge is allowed only after its durable recovery key is committed.
	frappe.db.commit()
	return txn


def _save_terminal_transaction(attempt, terminal: str, operation_id: str, result, operation: str = "Sale"):
	txn = _ensure_terminal_transaction(attempt, terminal, operation_id, operation)
	txn.status = result.status.title()
	txn.rrn = result.rrn
	txn.invoice_number = result.invoice_number
	txn.auth_code = result.auth_code
	txn.card_mask = result.card_mask
	txn.response_json = frappe.as_json(_masked_terminal_payload(result.raw))
	txn.save(ignore_permissions=True)
	attempt.terminal_transaction = txn.name
	attempt.status = "Confirmed" if result.status == "confirmed" else ("Declined" if result.status == "declined" else "Unknown")
	attempt.save(ignore_permissions=True)
	return txn


def _validate_order_items(order):
	if order.order_type == "Return":
		return
	requested = defaultdict(float)
	for row in order.items:
		item = frappe.db.get_value(
			"Item",
			row.item_code,
			["is_stock_item", "has_batch_no", "has_serial_no", "ua_serial_mode"],
			as_dict=True,
		)
		if not item:
			frappe.throw(_("Товар {0} не знайдено").format(row.item_code))
		if item.has_batch_no and not row.batch_no:
			frappe.throw(_("Для товару {0} обов’язково вкажіть партію").format(row.item_code))
		if item.has_serial_no or item.ua_serial_mode == "Strict":
			serials = [value.strip() for value in (row.serial_no or "").replace(",", "\n").splitlines() if value.strip()]
			if len(serials) != int(frappe.utils.flt(row.qty)):
				frappe.throw(_("Для товару {0} потрібно вказати {1} серійних номерів").format(row.item_code, row.qty))
		if item.is_stock_item:
			requested[(row.item_code, row.warehouse)] += frappe.utils.flt(row.qty)
	for (item_code, warehouse), qty in requested.items():
		available = frappe.utils.flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty"))
		if qty > available + 0.000001:
			frappe.throw(
				_("Недостатньо товару {0} на складі {1}: доступно {2}, потрібно {3}").format(
					item_code, warehouse, available, qty
				)
			)


@frappe.whitelist()
def create_draft_invoice(pos_session_token: str, order: str) -> dict:
	"""Convert a cart to a draft non-stock Sales Invoice without posting or stock movement."""
	session = get_session(pos_session_token)
	doc = _owned_order(session, order)
	if doc.draft_invoice:
		si = frappe.get_doc("Sales Invoice", doc.draft_invoice)
		return {"name": si.name, "grand_total": si.grand_total, "docstatus": si.docstatus, "order": doc.name}
	if doc.status != "Building" or doc.order_type != "Sale":
		frappe.throw(_("Рахунок можна створити лише з активного чека продажу"))
	if not doc.items:
		frappe.throw(_("Додайте хоча б один товар"))
	desk = frappe.get_doc("POS Cash Desk", doc.cash_desk)
	if not doc.customer or doc.customer == desk.default_customer:
		frappe.throw(_("Спочатку виберіть або ідентифікуйте покупця"))
	invoice_items = []
	for row in doc.items:
		gross = frappe.utils.flt(row.qty) * frappe.utils.flt(row.rate)
		discount_percentage = frappe.utils.flt(row.discount_amount) * 100 / gross if gross else 0
		invoice_items.append(
			{
				"item_code": row.item_code,
				"qty": row.qty,
				"uom": row.uom,
				"price_list_rate": row.rate,
				"rate": row.rate,
				"discount_percentage": discount_percentage,
				"warehouse": row.warehouse,
			}
		)
	si = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": desk.company,
			"customer": doc.customer,
			"is_pos": 0,
			"update_stock": 0,
			"ignore_pricing_rule": 1,
			"set_warehouse": desk.warehouse,
			"ua_pos_order": doc.name,
			"ua_pos_desk": desk.name,
			"ua_pos_shift": doc.operational_shift,
			"remarks": _("Створено з вікна касира {0}. Товар не видано, склад не списано.").format(doc.name),
			"items": invoice_items,
		}
	)
	si.set_missing_values()
	si.insert(ignore_permissions=True)
	doc.draft_invoice = si.name
	doc.status = "Invoice Draft"
	doc.save(ignore_permissions=True)
	audit("draft_invoice_created", session, (doc.doctype, doc.name), {"sales_invoice": si.name})
	frappe.db.commit()
	return {"name": si.name, "grand_total": si.grand_total, "docstatus": si.docstatus, "order": doc.name}


def _post_sales_invoice(order, desk):
	is_return = order.order_type == "Return"
	original_invoice = frappe.db.get_value("POS Order", order.return_against, "sales_invoice") if is_return else None
	if is_return and not original_invoice:
		frappe.throw(_("Первинний чек не має проведеного Sales Invoice"))
	si = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": desk.company,
			"customer": order.customer,
			"is_pos": 1,
			"update_stock": 1,
			"is_return": 1 if is_return else 0,
			"return_against": original_invoice,
			"set_warehouse": desk.warehouse,
			"ua_pos_order": order.name,
			"ua_pos_desk": desk.name,
			"ua_pos_shift": order.operational_shift,
			"items": [
				{
					"item_code": row.item_code,
					"qty": -row.qty if is_return else row.qty,
					"uom": row.uom,
					"rate": row.rate,
					"warehouse": row.warehouse,
					"batch_no": row.batch_no,
					"serial_no": row.serial_no,
				}
				for row in order.items
			],
			"payments": [
				{"mode_of_payment": row.mode_of_payment, "amount": -row.amount if is_return else row.amount}
				for row in order.payments_plan
				if row.status == "Confirmed"
			],
		}
	)
	si.set_missing_values()
	si.insert(ignore_permissions=True)
	si.submit()
	return si


def _cash_movements(order, session):
	for payment in order.payments_plan:
		if payment.status != "Confirmed":
			continue
		movement_idem = f"order-payment:{order.name}:{payment.name}"
		if frappe.db.exists("POS Cash Movement", {"idem_key": movement_idem}):
			continue
		is_cash = payment.kind == "Cash"
		is_return = order.order_type == "Return"
		frappe.get_doc(
			{
				"doctype": "POS Cash Movement",
				"cash_desk": order.cash_desk,
				"operational_shift": order.operational_shift,
				"employee": session["employee"],
				"direction": "Out" if is_return else "In",
				"movement_type": "Refund Cash" if (is_return and is_cash) else ("Sale Cash" if is_cash else "Deposit"),
				"amount": payment.amount,
				"currency": payment.currency,
				"mode_of_payment": payment.mode_of_payment,
				"is_cash_drawer": 1 if is_cash else 0,
				"basis_doctype": "POS Order",
				"basis_name": order.name,
				"idem_key": movement_idem,
			}
		).insert(ignore_permissions=True).submit()


def _fiscalize(order, desk, si):
	if order.fiscal_mode != "Fiscal" or not desk.prro_cash_register:
		return None
	from erpnext_ua.ua_fiscal import orchestration
	from erpnext_ua.ua_fiscal.sales_invoice import _invoice_lines, _invoice_taxes

	register = frappe.get_doc("PRRO Cash Register", desk.prro_cash_register)
	key = desk.default_kep_key or register.default_kep_key
	if not register.current_shift:
		orchestration.open_shift(register.name, key)
	# Фіскальні суми беремо з проведеного Sales Invoice: там уже враховані
	# документні знижки й розподіл податків. Штрихкод лишається з POS Order.
	items = _invoice_lines(si)
	for item, order_row in zip(items, order.items, strict=False):
		item["barcode"] = order_row.barcode or item.get("barcode")
	payments = []
	for payment in order.payments_plan:
		if payment.status != "Confirmed":
			continue
		configured_code = frappe.db.get_value("Mode of Payment", payment.mode_of_payment, "ua_payformcd")
		row = {
			"code": int(configured_code) if configured_code not in (None, "") else (0 if payment.kind == "Cash" else 1),
			"name": payment.mode_of_payment,
			"sum": payment.amount,
		}
		if payment.kind == "Cash":
			row["provided"] = payment.tendered_amount or payment.amount
			row["remains"] = payment.change_amount or 0
		if payment.kind == "Card" and payment.payment_attempt:
			attempt = frappe.get_doc("POS Payment Attempt", payment.payment_attempt)
			if attempt.terminal_transaction:
				txn = frappe.get_doc("Terminal Transaction", attempt.terminal_transaction)
				row["paysys"] = [
					{
						"transaction_id": txn.operation_id,
						"transaction_date": frappe.utils.get_datetime(txn.creation).isoformat(),
						"transaction_number": txn.invoice_number or txn.rrn,
						"device_id": txn.terminal,
						"epz_details": txn.card_mask,
						"auth_code": txn.auth_code,
						"sum": payment.amount,
					}
				]
		payments.append(row)
	total = abs(frappe.utils.flt(order.grand_total))
	no_rounding_total = abs(frappe.utils.flt(si.grand_total))
	has_rounding = abs(total - no_rounding_total) > 0.001
	return orchestration.fiscalize_sale(
		cash_register=register.name,
		kep_key=key,
		items=items,
		payments=payments,
		total=total,
		taxes=_invoice_taxes(si),
		no_rounding_total=no_rounding_total if has_rounding else None,
		rounding_sum=(no_rounding_total - total) if has_rounding else None,
		sales_invoice=si.name,
		receipt_type="Повернення" if order.order_type == "Return" else "Продаж",
		related_receipt=(
			frappe.db.get_value("POS Order", order.return_against, "prro_receipt")
			if order.order_type == "Return"
			else None
		),
		pos_order=order.name,
		idem_key=f"{'return' if order.order_type == 'Return' else 'sale'}:{register.name}:{si.name}",
	)


def _completed_returns(original_order: str) -> list[str]:
	return frappe.get_all(
		"POS Order",
		filters={
			"return_against": original_order,
			"order_type": "Return",
			"status": ("in", tuple(FINAL_ORDER_STATUSES)),
		},
		pluck="name",
	)


def _return_summary(original) -> dict:
	return_names = _completed_returns(original.name)
	returned_by_item = defaultdict(float)
	refunded_by_kind = defaultdict(float)
	if return_names:
		for row in frappe.get_all(
			"POS Order Item",
			filters={"parent": ("in", return_names)},
			fields=["return_against_item", "qty"],
		):
			returned_by_item[row.return_against_item] += frappe.utils.flt(row.qty)
		for row in frappe.get_all(
			"POS Order Payment",
			filters={"parent": ("in", return_names), "status": "Confirmed"},
			fields=["kind", "amount"],
		):
			refunded_by_kind[row.kind] += frappe.utils.flt(row.amount)
	items = []
	for row in original.items:
		available = max(0, frappe.utils.flt(row.qty) - returned_by_item[row.name])
		items.append(
			{
				"row_name": row.name,
				"item_code": row.item_code,
				"item_name": row.item_name,
				"sold_qty": row.qty,
				"returned_qty": returned_by_item[row.name],
				"available_qty": available,
				"uom": row.uom,
				"rate": row.rate,
				"amount": row.amount,
			}
		)
	paid_by_kind = defaultdict(float)
	mode_by_kind = {}
	for row in original.payments_plan:
		if row.status == "Confirmed":
			paid_by_kind[row.kind] += frappe.utils.flt(row.amount)
			mode_by_kind.setdefault(row.kind, row.mode_of_payment)
	refund_limits = [
		{
			"kind": kind,
			"mode_of_payment": mode_by_kind[kind],
			"paid": amount,
			"refunded": refunded_by_kind[kind],
			"available": max(0, amount - refunded_by_kind[kind]),
		}
		for kind, amount in paid_by_kind.items()
	]
	return {"items": items, "refund_limits": refund_limits}


@frappe.whitelist()
def return_details(pos_session_token: str, token: str) -> dict:
	session = get_session(pos_session_token)
	name = frappe.db.get_value(
		"POS Order",
		{"lookup_token": (token or "").strip(), "status": ("in", tuple(FINAL_ORDER_STATUSES))},
		"name",
	)
	if not name:
		frappe.throw(_("Первинний чек не знайдено"))
	original = frappe.get_doc("POS Order", name)
	if original.order_type == "Return":
		frappe.throw(_("Повернення можна оформити лише за чеком продажу"))
	if original.cash_desk != session["cash_desk"]:
		frappe.throw(_("Первинний чек належить іншій касі"), frappe.PermissionError)
	return {"order": original.as_dict(), **_return_summary(original)}


@frappe.whitelist()
def create_return_order(pos_session_token: str, token: str, items, idem_key: str) -> dict:
	session = get_session(pos_session_token)
	shift = _require_shift(session)
	existing = frappe.db.get_value("POS Order", {"idem_key": idem_key}, "name")
	if existing:
		return frappe.get_doc("POS Order", existing).as_dict()
	details = return_details(pos_session_token, token)
	original = frappe.get_doc("POS Order", details["order"]["name"])
	available = {row["row_name"]: row for row in details["items"]}
	requested = parse_rows(items)
	if not requested:
		frappe.throw(_("Оберіть хоча б один товар для повернення"))
	desk = frappe.get_doc("POS Cash Desk", session["cash_desk"])
	doc = frappe.get_doc(
		{
			"doctype": "POS Order",
			"cash_desk": desk.name,
			"operational_shift": shift,
			"employee": session["employee"],
			"customer": original.customer,
			"order_type": "Return",
			"return_against": original.name,
			"fiscal_mode": original.fiscal_mode,
			"lookup_token": str(uuid.uuid4()),
			"idem_key": idem_key,
		}
	)
	for requested_row in requested:
		row_name = requested_row.get("row_name")
		qty = frappe.utils.flt(requested_row.get("qty"))
		info = available.get(row_name)
		if not info or qty <= 0 or qty > frappe.utils.flt(info["available_qty"]) + 0.000001:
			frappe.throw(_("Некоректна кількість повернення для рядка {0}").format(row_name))
		original_row = next(row for row in original.items if row.name == row_name)
		discount_per_unit = frappe.utils.flt(original_row.discount_amount) / frappe.utils.flt(original_row.qty)
		doc.append(
			"items",
			{
				"item_code": original_row.item_code,
				"item_name": original_row.item_name,
				"image": original_row.image,
				"barcode": original_row.barcode,
				"qty": qty,
				"uom": original_row.uom,
				"rate": original_row.rate,
				"warehouse": original_row.warehouse,
				"batch_no": original_row.batch_no,
				"serial_no": original_row.serial_no,
				"discount_amount": frappe.utils.flt(discount_per_unit * qty, 2),
				"fop_profile": original_row.fop_profile,
				"return_against_item": original_row.name,
			},
		)
	doc.insert(ignore_permissions=True)
	audit("return_created", session, (doc.doctype, doc.name), {"return_against": original.name})
	return doc.as_dict()


def _validate_return_payments(order, payment_rows: list[dict]):
	original = frappe.get_doc("POS Order", order.return_against)
	limits = {row["kind"]: frappe.utils.flt(row["available"]) for row in _return_summary(original)["refund_limits"]}
	requested = defaultdict(float)
	for row in payment_rows:
		requested[row.get("kind")] += frappe.utils.flt(row.get("amount"))
	for kind, amount in requested.items():
		if amount > limits.get(kind, 0) + 0.001:
			frappe.throw(_("Сума повернення способом {0} перевищує доступний ліміт {1}").format(kind, limits.get(kind, 0)))


def _record_birthday_benefit(order):
	if order.order_type != "Sale" or not int(order.birthday_benefit_year or 0):
		return
	usage_key = f"{order.customer}:{int(order.birthday_benefit_year)}"
	if frappe.db.exists("POS Birthday Benefit", {"usage_key": usage_key}):
		return
	frappe.get_doc(
		{
			"doctype": "POS Birthday Benefit",
			"customer": order.customer,
			"benefit_year": int(order.birthday_benefit_year),
			"pos_order": order.name,
			"discount_percent": order.birthday_discount_percent,
			"used_at": frappe.utils.now_datetime(),
			"usage_key": usage_key,
		}
	).insert(ignore_permissions=True)


def _complete_paid_order(doc, desk, session) -> dict:
	if not doc.sales_invoice:
		doc.status = "Posting"
		doc.save(ignore_permissions=True)
	try:
		if doc.sales_invoice:
			si = frappe.get_doc("Sales Invoice", doc.sales_invoice)
		else:
			si = _post_sales_invoice(doc, desk)
			doc.sales_invoice = si.name
			doc.status = "Posted"
			doc.save(ignore_permissions=True)
		_cash_movements(doc, session)
		try:
			receipt = _fiscalize(doc, desk, si)
		except Exception as exc:
			doc.status = "Fiscal Pending"
			doc.recovery_note = str(exc)[:500]
		else:
			doc.prro_receipt = receipt
			receipt_status = frappe.db.get_value("PRRO Receipt", receipt, "status") if receipt else None
			doc.status = "Completed" if not receipt or receipt_status in {"Fiscalized", "Offline"} else "Fiscal Pending"
			if doc.status == "Fiscal Pending":
				doc.recovery_note = f"Фіскальний документ {receipt} має статус {receipt_status}"
		doc.save(ignore_permissions=True)
		if doc.status == "Completed":
			_queue_print_if_configured(doc)
		_record_birthday_benefit(doc)
	except Exception as exc:
		doc.status = "Manual Review"
		doc.recovery_note = str(exc)[:500]
		doc.save(ignore_permissions=True)
		raise
	audit(
		"return_completed" if doc.order_type == "Return" else "sale_completed",
		session,
		(doc.doctype, doc.name),
		{"sales_invoice": doc.sales_invoice},
	)
	frappe.db.commit()
	return doc.as_dict()


def _queue_print_if_configured(doc):
	"""Attach the immutable original receipt print job without affecting the sale transaction."""
	from erpnext_ua.ua_pos.print_service import queue_order_receipt

	try:
		job = queue_order_receipt(doc, is_copy=False)
	except Exception as exc:
		doc.status = "Completed Print Error"
		doc.recovery_note = str(exc)[:500]
		doc.save(ignore_permissions=True)
		frappe.log_error(frappe.get_traceback(), f"POS print queue {doc.name}")
		return None
	if not job:
		return None
	if job.status in {"Queued", "Printing"}:
		doc.status = "Printing"
	elif job.status == "Failed":
		doc.status = "Completed Print Error"
		doc.recovery_note = job.error_message
	else:
		doc.status = "Completed"
		doc.recovery_note = None
	doc.save(ignore_permissions=True)
	return job


@frappe.whitelist()
def retry_fiscalization(pos_session_token: str, order: str) -> dict:
	session = get_session(pos_session_token)
	doc = _owned_order(session, order, {"Fiscal Pending", "Posted", "Manual Review"})
	if not doc.sales_invoice:
		frappe.throw(_("Sales Invoice ще не створено"))
	failed_receipt = doc.prro_receipt or frappe.db.get_value(
		"PRRO Receipt", {"pos_order": doc.name}, "name", order_by="local_number desc"
	)
	if failed_receipt and frappe.db.get_value("PRRO Receipt", failed_receipt, "status") == "Error":
		# Reconcile спершу перевіряє DocumentInfoByLocalNum. Повтор дозволяється
		# лише якщо registrar state підтвердив, що останній номер не спожито.
		from erpnext_ua.ua_fiscal.orchestration import reconcile_receipt

		reconcile_receipt(failed_receipt)
	desk = frappe.get_doc("POS Cash Desk", doc.cash_desk)
	receipt = _fiscalize(doc, desk, frappe.get_doc("Sales Invoice", doc.sales_invoice))
	status = frappe.db.get_value("PRRO Receipt", receipt, "status") if receipt else None
	doc.prro_receipt = receipt
	doc.status = "Completed" if not receipt or status in {"Fiscalized", "Offline"} else "Fiscal Pending"
	doc.recovery_note = None if doc.status == "Completed" else f"Фіскальний документ {receipt}: {status}"
	doc.save(ignore_permissions=True)
	if doc.status == "Completed":
		_queue_print_if_configured(doc)
	frappe.db.commit()
	return doc.as_dict()


def recover_pos_fiscal_pending():
	for name in frappe.get_all("POS Order", filters={"status": "Fiscal Pending"}, pluck="name", limit=50):
		try:
			doc = frappe.get_doc("POS Order", name)
			if not doc.sales_invoice:
				continue
			desk = frappe.get_doc("POS Cash Desk", doc.cash_desk)
			receipt = _fiscalize(doc, desk, frappe.get_doc("Sales Invoice", doc.sales_invoice))
			status = frappe.db.get_value("PRRO Receipt", receipt, "status") if receipt else None
			if not receipt or status in {"Fiscalized", "Offline"}:
				doc.db_set("prro_receipt", receipt, update_modified=False)
				doc.status = "Completed"
				doc.recovery_note = None
				doc.save(ignore_permissions=True)
				_queue_print_if_configured(doc)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"POS fiscal recovery {name}")


@frappe.whitelist()
def checkout_start(pos_session_token: str, order: str, payments, idem_key: str) -> dict:
	session = get_session(pos_session_token)
	frappe.db.sql("select name from `tabPOS Order` where name=%s for update", order)
	doc = _owned_order(session, order)
	if doc.status not in {"Building", "Awaiting Payment"}:
		return doc.as_dict()
	if not doc.items or doc.grand_total <= 0:
		frappe.throw(_("У чеку немає товарів до оплати"))
	_validate_order_items(doc)
	payment_rows = parse_rows(payments)
	if not payment_rows:
		frappe.throw(_("Вкажіть спосіб оплати"))
	for row in payment_rows:
		if row.get("kind") not in {"Cash", "Card", "IBAN", "Bonus", "Installment"}:
			frappe.throw(_("Некоректний тип оплати"))
		if not frappe.db.exists("Mode of Payment", row.get("mode_of_payment")):
			frappe.throw(_("Спосіб оплати {0} не знайдено").format(row.get("mode_of_payment")))
	if abs(sum(frappe.utils.flt(row.get("amount")) for row in payment_rows) - doc.grand_total) > 0.01:
		frappe.throw(_("Сума оплат має дорівнювати сумі чека"))
	if doc.fiscal_mode == "Non Fiscal" and len(payment_rows) > 1:
		frappe.throw(_("Змішана оплата доступна лише для фіскального продажу"))
	if doc.order_type == "Return":
		_validate_return_payments(doc, payment_rows)
		cash_refund = sum(frappe.utils.flt(row.get("amount")) for row in payment_rows if row.get("kind") == "Cash")
		if cash_refund > _cash_balance(doc.operational_shift) + 0.001:
			frappe.throw(_("У касі недостатньо готівки для повернення"))
	desk = frappe.get_doc("POS Cash Desk", doc.cash_desk)
	doc.status = "Payment In Progress"
	doc.payments_plan = []
	doc.save(ignore_permissions=True)
	unknown = False
	for number, row in enumerate(payment_rows, 1):
		row["amount"] = frappe.utils.flt(row["amount"])
		tendered = frappe.utils.flt(row.get("tendered_amount") or row["amount"])
		if row["kind"] == "Cash" and doc.order_type != "Return" and tendered < row["amount"]:
			frappe.throw(_("Отримана готівка менша за суму оплати"))
		row["tendered_amount"] = tendered
		row["change_amount"] = max(0, tendered - row["amount"]) if doc.order_type != "Return" else 0
		attempt = _attempt(doc, row, number, idem_key)
		status = "Confirmed"
		if row["kind"] == "Card":
			if not desk.terminal:
				frappe.throw(_("Для цієї каси не налаштовано банківський термінал"))
			attempt.status = "Sent"
			attempt.save(ignore_permissions=True)
			operation_id = f"{doc.name}-{number}-{digest(idem_key)[:12]}"
			operation = "Refund" if doc.order_type == "Return" else "Sale"
			_ensure_terminal_transaction(attempt, desk.terminal, operation_id, operation)
			try:
				if doc.order_type == "Return":
					original_attempt = frappe.get_all(
						"POS Payment Attempt",
						filters={"pos_order": doc.return_against, "kind": "Card", "status": "Confirmed"},
						fields=["terminal_transaction"],
						order_by="attempt_no",
						limit=1,
					)
					if not original_attempt or not original_attempt[0].terminal_transaction:
						frappe.throw(_("Не знайдено первинну транзакцію термінала"))
					original_txn = frappe.get_doc("Terminal Transaction", original_attempt[0].terminal_transaction)
					reference = original_txn.invoice_number or original_txn.operation_id
					result = get_adapter().refund(resolve_terminal(desk.terminal), row["amount"], operation_id, reference)
				else:
					result = get_adapter().sale(resolve_terminal(desk.terminal), row["amount"], operation_id)
			except Exception as exc:
				attempt.status = "Unknown"
				attempt.error_text = str(exc)[:500]
				attempt.save(ignore_permissions=True)
				status, unknown = "Failed", True
			else:
				_save_terminal_transaction(
					attempt,
					desk.terminal,
					operation_id,
					result,
					operation=operation,
				)
				status = "Confirmed" if result.status == "confirmed" else "Failed"
				unknown = unknown or result.status == "unknown"
		else:
			attempt.status = "Confirmed"
			attempt.save(ignore_permissions=True)
		doc.append("payments_plan", {**row, "status": status, "payment_attempt": attempt.name})
	doc.status = "Payment Unknown" if unknown else ("Paid" if all(r.status == "Confirmed" for r in doc.payments_plan) else "Awaiting Payment")
	doc.save(ignore_permissions=True)
	if doc.status != "Paid":
		frappe.db.commit()
		return doc.as_dict()
	return _complete_paid_order(doc, desk, session)


@frappe.whitelist()
def card_status(pos_session_token: str, attempt: str) -> dict:
	session = get_session(pos_session_token)
	doc = frappe.get_doc("POS Payment Attempt", attempt)
	order = frappe.get_doc("POS Order", doc.pos_order)
	if order.cash_desk != session["cash_desk"]:
		frappe.throw(_("Оплата належить іншій касі"), frappe.PermissionError)
	if doc.status not in {"Unknown", "Timeout", "Sent"}:
		return {"attempt": doc.as_dict(), "order": order.as_dict()}
	txn = frappe.get_doc("Terminal Transaction", doc.terminal_transaction) if doc.terminal_transaction else None
	operation_id = txn.operation_id if txn else doc.idem_key
	terminal = frappe.db.get_value("POS Cash Desk", order.cash_desk, "terminal")
	result = get_adapter().status(resolve_terminal(terminal), operation_id)
	if txn:
		txn.status = result.status.title()
		txn.rrn = result.rrn or txn.rrn
		txn.invoice_number = result.invoice_number or txn.invoice_number
		txn.auth_code = result.auth_code or txn.auth_code
		txn.card_mask = result.card_mask or txn.card_mask
		txn.response_json = frappe.as_json(_masked_terminal_payload(result.raw))
		txn.save(ignore_permissions=True)
	doc.status = (
		"Confirmed"
		if result.status == "confirmed"
		else ("Declined" if result.status in {"declined", "cancelled"} else "Unknown")
	)
	doc.save(ignore_permissions=True)
	for payment in order.payments_plan:
		if payment.payment_attempt == doc.name:
			payment.status = "Confirmed" if doc.status == "Confirmed" else ("Failed" if doc.status == "Declined" else payment.status)
			break
	if all(row.status == "Confirmed" for row in order.payments_plan):
		order.status = "Paid"
	elif doc.status == "Declined":
		order.status = "Awaiting Payment"
	else:
		order.status = "Payment Unknown"
	order.save(ignore_permissions=True)
	if order.status == "Paid":
		order_payload = _complete_paid_order(order, frappe.get_doc("POS Cash Desk", order.cash_desk), session)
	else:
		frappe.db.commit()
		order_payload = order.as_dict()
	return {"attempt": doc.as_dict(), "order": order_payload}


@frappe.whitelist()
def lookup_return(pos_session_token: str, token: str) -> dict:
	return return_details(pos_session_token, token)["order"]


@frappe.whitelist()
def receipt_data(pos_session_token: str, order: str) -> dict:
	session = get_session(pos_session_token)
	doc = _owned_order(session, order)
	if doc.status not in FINAL_ORDER_STATUSES | {"Fiscal Pending", "Posted"}:
		frappe.throw(_("Чек ще не завершено"))
	desk = frappe.get_doc("POS Cash Desk", doc.cash_desk)
	company = frappe.db.get_value(
		"Company", desk.company, ["company_name", "tax_id", "company_description"], as_dict=True
	) or {}
	employee_name = frappe.db.get_value("Employee", doc.employee, "employee_name") or doc.employee
	fiscal_receipt = None
	if doc.fiscal_mode == "Fiscal":
		if not doc.prro_receipt:
			frappe.throw(_("Фіскальний чек ще не створено. Спочатку виконайте відновлення фіскалізації."))
		fiscal_receipt = frappe.db.get_value(
			"PRRO Receipt",
			doc.prro_receipt,
			["name", "status", "fiscal_number", "local_number", "is_offline", "qr_data", "fiscalized_at"],
			as_dict=True,
		)
		if not fiscal_receipt or fiscal_receipt.status not in {"Fiscalized", "Offline"}:
			frappe.throw(_("Фіскальний документ ще не підтверджено і його не можна друкувати"))
	return {
		"order": doc.as_dict(),
		"company": company,
		"cash_desk": desk.desk_name,
		"employee_name": employee_name,
		"fiscal_receipt": fiscal_receipt,
		"printed_at": str(frappe.utils.now_datetime()),
	}


@frappe.whitelist()
def queue_receipt_print(pos_session_token: str, order: str, idem_key: str) -> dict:
	"""Queue original/reprint safely; a repeat request with the same key returns the same job."""
	session = get_session(pos_session_token)
	doc = _owned_order(
		session,
		order,
		{"Printing", "Completed", "Completed Print Error"},
	)
	from erpnext_ua.ua_pos.print_service import queue_order_receipt

	has_original = bool(
		frappe.db.exists("POS Print Job", {"idem_key": f"receipt:{doc.name}:original"})
	)
	job = queue_order_receipt(doc, is_copy=has_original, idem_key=idem_key)
	if not job:
		return {"fallback_browser": True, "reason": "Для каси не налаштовано мережевий чековий принтер"}
	if has_original:
		audit("reprint_queued", session, (doc.doctype, doc.name), {"print_job": job.name})
	elif job.status in {"Queued", "Printing"}:
		doc.db_set("status", "Printing", update_modified=False)
	frappe.db.commit()
	return {"fallback_browser": False, "job": job.as_dict(), "is_copy": has_original}
