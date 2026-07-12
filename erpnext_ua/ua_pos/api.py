from __future__ import annotations

import json
import secrets
import uuid

import frappe
from frappe import _

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


@frappe.whitelist(allow_guest=False)
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
	unfinished = frappe.get_all(
		"POS Order",
		filters={"cash_desk": session["cash_desk"], "status": ("not in", ("Completed", "Cancelled"))},
		fields=["name", "status", "grand_total", "modified"],
		order_by="modified desc",
		limit=10,
	)
	return {**session, "shift": shift, "unfinished_orders": unfinished}


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
		filters={"operational_shift": shift, "status": ("not in", ("Completed", "Cancelled"))},
		pluck="name",
	)
	return {"shift": shift, "expected": _expected_cash(shift), "blocking_orders": blocking}


@frappe.whitelist()
def close_shift_confirm(pos_session_token: str, denominations, idem_key: str, comment: str = "") -> dict:
	session = get_session(pos_session_token)
	rows = parse_rows(denominations)
	shift_name = active_shift(session["cash_desk"], for_update=True)
	if not shift_name:
		frappe.throw("No open shift")
	if frappe.db.exists("POS Order", {"operational_shift": shift_name, "status": ("not in", ("Completed", "Cancelled"))}):
		frappe.throw("Resolve unfinished POS orders before closing the shift")
	doc = frappe.get_doc("POS Operational Shift", shift_name)
	expected, counted = _expected_cash(shift_name), _count_total(rows)
	discrepancy = counted - expected
	if discrepancy and not comment.strip():
		frappe.throw("A cashier comment is required when cash differs from expected")
	doc.status = "Closed"
	doc.set("closing_counts", [{**row, "context": "Closing"} for row in rows])
	doc.expected_cash = expected
	doc.counted_cash = counted
	doc.discrepancy = discrepancy
	doc.closing_comment = comment
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
	doc = frappe.get_doc("POS Order", order)
	if doc.cash_desk != session["cash_desk"] or doc.status != "Building":
		frappe.throw("Order is not editable", frappe.PermissionError)
	item_code, barcode = _resolve_item(query.strip())
	item = frappe.db.get_value("Item", item_code, ["item_name", "stock_uom", "disabled"], as_dict=True)
	if item.disabled:
		frappe.throw("Item is disabled")
	desk = frappe.get_doc("POS Cash Desk", doc.cash_desk)
	rate = frappe.db.get_value("Item Price", {"item_code": item_code, "selling": 1}, "price_list_rate") or 0
	for row in doc.items:
		if row.item_code == item_code and not row.serial_no and not row.batch_no:
			row.qty += frappe.utils.flt(qty)
			doc.save(ignore_permissions=True)
			return doc.as_dict()
	doc.append(
		"items",
		{
			"item_code": item_code,
			"item_name": item.item_name,
			"barcode": barcode,
			"qty": frappe.utils.flt(qty),
			"uom": item.stock_uom,
			"rate": rate,
			"warehouse": desk.warehouse,
		},
	)
	doc.save(ignore_permissions=True)
	return doc.as_dict()


@frappe.whitelist()
def set_item_qty(pos_session_token: str, order: str, row_name: str, qty: float) -> dict:
	session = get_session(pos_session_token)
	doc = frappe.get_doc("POS Order", order)
	if doc.cash_desk != session["cash_desk"] or doc.status != "Building":
		frappe.throw("Order is not editable", frappe.PermissionError)
	row = next((row for row in doc.items if row.name == row_name), None)
	if not row:
		frappe.throw("Order item not found")
	if frappe.utils.flt(qty) <= 0:
		doc.remove(row)
	else:
		row.qty = frappe.utils.flt(qty)
	doc.save(ignore_permissions=True)
	return doc.as_dict()


@frappe.whitelist()
def get_order(pos_session_token: str, order: str) -> dict:
	session = get_session(pos_session_token)
	doc = frappe.get_doc("POS Order", order)
	if doc.cash_desk != session["cash_desk"]:
		frappe.throw("Order belongs to another cash desk", frappe.PermissionError)
	return doc.as_dict()


def _attempt(order, payment: dict, number: int, idem_key: str):
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


def _save_terminal_transaction(attempt, terminal: str, operation_id: str, result):
	txn = frappe.get_doc(
		{
			"doctype": "Terminal Transaction",
			"payment_attempt": attempt.name,
			"terminal": terminal,
			"operation": "Sale",
			"operation_id": operation_id,
			"amount": attempt.amount,
			"currency": attempt.currency,
			"status": result.status.title(),
			"rrn": result.rrn,
			"invoice_number": result.invoice_number,
			"auth_code": result.auth_code,
			"card_mask": result.card_mask,
			"response_json": frappe.as_json(result.raw),
		}
	).insert(ignore_permissions=True)
	attempt.terminal_transaction = txn.name
	attempt.status = "Confirmed" if result.status == "confirmed" else ("Declined" if result.status == "declined" else "Unknown")
	attempt.save(ignore_permissions=True)
	return txn


def _post_sales_invoice(order, desk):
	si = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": desk.company,
			"customer": order.customer,
			"is_pos": 1,
			"update_stock": 1,
			"set_warehouse": desk.warehouse,
			"ua_pos_order": order.name,
			"ua_pos_desk": desk.name,
			"ua_pos_shift": order.operational_shift,
			"items": [
				{
					"item_code": row.item_code,
					"qty": row.qty,
					"uom": row.uom,
					"rate": row.rate,
					"warehouse": row.warehouse,
					"batch_no": row.batch_no,
					"serial_no": row.serial_no,
				}
				for row in order.items
			],
			"payments": [
				{"mode_of_payment": row.mode_of_payment, "amount": row.amount}
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
		is_cash = payment.kind == "Cash"
		frappe.get_doc(
			{
				"doctype": "POS Cash Movement",
				"cash_desk": order.cash_desk,
				"operational_shift": order.operational_shift,
				"employee": session["employee"],
				"direction": "In",
				"movement_type": "Sale Cash" if is_cash else "Deposit",
				"amount": payment.amount,
				"currency": payment.currency,
				"mode_of_payment": payment.mode_of_payment,
				"is_cash_drawer": 1 if is_cash else 0,
				"basis_doctype": "POS Order",
				"basis_name": order.name,
			}
		).insert(ignore_permissions=True).submit()


def _fiscalize(order, desk, si):
	if order.fiscal_mode != "Fiscal" or not desk.prro_cash_register:
		return None
	from erpnext_ua.ua_fiscal import orchestration

	register = frappe.get_doc("PRRO Cash Register", desk.prro_cash_register)
	key = desk.default_kep_key or register.default_kep_key
	if not register.current_shift:
		orchestration.open_shift(register.name, key)
	return orchestration.fiscalize_sale(
		cash_register=register.name,
		kep_key=key,
		items=[{"code": r.item_code, "name": r.item_name, "uom": r.uom, "qty": r.qty, "price": r.rate, "amount": r.amount} for r in order.items],
		payments=[{"code": 0 if p.kind == "Cash" else 1, "name": p.mode_of_payment, "sum": p.amount} for p in order.payments_plan if p.status == "Confirmed"],
		total=order.grand_total,
		sales_invoice=si.name,
	)


@frappe.whitelist()
def checkout_start(pos_session_token: str, order: str, payments, idem_key: str) -> dict:
	session = get_session(pos_session_token)
	frappe.db.sql("select name from `tabPOS Order` where name=%s for update", order)
	doc = frappe.get_doc("POS Order", order)
	if doc.cash_desk != session["cash_desk"]:
		frappe.throw("Order belongs to another cash desk", frappe.PermissionError)
	if doc.status not in {"Building", "Awaiting Payment"}:
		return doc.as_dict()
	if not doc.items or doc.grand_total <= 0:
		frappe.throw("Order has no payable items")
	payment_rows = parse_rows(payments)
	if abs(sum(frappe.utils.flt(row.get("amount")) for row in payment_rows) - doc.grand_total) > 0.01:
		frappe.throw("Payment total must equal order total")
	desk = frappe.get_doc("POS Cash Desk", doc.cash_desk)
	doc.status = "Payment In Progress"
	doc.payments_plan = []
	doc.save(ignore_permissions=True)
	unknown = False
	for number, row in enumerate(payment_rows, 1):
		row["amount"] = frappe.utils.flt(row["amount"])
		attempt = _attempt(doc, row, number, idem_key)
		status = "Confirmed"
		if row["kind"] == "Card":
			if not desk.terminal:
				frappe.throw("No bank terminal configured for this cash desk")
			attempt.status = "Sent"
			attempt.save(ignore_permissions=True)
			operation_id = f"{doc.name}-{number}-{digest(idem_key)[:12]}"
			try:
				result = get_adapter().sale(resolve_terminal(desk.terminal), row["amount"], operation_id)
			except Exception as exc:
				attempt.status = "Unknown"
				attempt.error_text = str(exc)[:500]
				attempt.save(ignore_permissions=True)
				status, unknown = "Failed", True
			else:
				_save_terminal_transaction(attempt, desk.terminal, operation_id, result)
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

	doc.status = "Posting"
	doc.save(ignore_permissions=True)
	try:
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
			doc.status = "Completed"
		doc.save(ignore_permissions=True)
	except Exception as exc:
		doc.status = "Manual Review"
		doc.recovery_note = str(exc)[:500]
		doc.save(ignore_permissions=True)
		raise
	audit("sale_completed", session, (doc.doctype, doc.name), {"sales_invoice": doc.sales_invoice})
	frappe.db.commit()
	return doc.as_dict()


@frappe.whitelist()
def card_status(pos_session_token: str, attempt: str) -> dict:
	session = get_session(pos_session_token)
	doc = frappe.get_doc("POS Payment Attempt", attempt)
	order = frappe.get_doc("POS Order", doc.pos_order)
	if order.cash_desk != session["cash_desk"]:
		frappe.throw("Payment belongs to another cash desk", frappe.PermissionError)
	if doc.status not in {"Unknown", "Timeout", "Sent"}:
		return doc.as_dict()
	txn = frappe.get_doc("Terminal Transaction", doc.terminal_transaction) if doc.terminal_transaction else None
	operation_id = txn.operation_id if txn else doc.idem_key
	terminal = frappe.db.get_value("POS Cash Desk", order.cash_desk, "terminal")
	result = get_adapter().status(resolve_terminal(terminal), operation_id)
	doc.status = "Confirmed" if result.status == "confirmed" else ("Declined" if result.status == "declined" else "Unknown")
	doc.save(ignore_permissions=True)
	return doc.as_dict()


@frappe.whitelist()
def lookup_return(pos_session_token: str, token: str) -> dict:
	get_session(pos_session_token)
	name = frappe.db.get_value("POS Order", {"lookup_token": token, "status": "Completed"}, "name")
	if not name:
		frappe.throw("Receipt not found")
	return frappe.get_doc("POS Order", name).as_dict()
