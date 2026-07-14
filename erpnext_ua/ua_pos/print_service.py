"""Durable server-side ESC/POS printing for network receipt printers."""

from __future__ import annotations

import base64
import socket
import textwrap
import xml.etree.ElementTree as ET

import frappe

from erpnext_ua.ua_pos.doctype.pos_printer.pos_printer import is_lan_address
from erpnext_ua.ua_pos.services.common import audit


MAX_PRINT_PAYLOAD = 128 * 1024
STALE_PRINT_MINUTES = 10


class EscPosReceipt:
	def __init__(self, *, width: int = 48, encoding: str = "cp1251", code_page: int = 46):
		self.width = max(16, min(int(width), 96))
		self.encoding = encoding
		self.parts = [b"\x1b@", b"\x1bt" + bytes([max(0, min(int(code_page), 255))])]

	def command(self, value: bytes):
		self.parts.append(value)

	def text(self, value="", *, align: str = "left", bold: bool = False):
		alignment = {"left": 0, "center": 1, "right": 2}[align]
		self.command(b"\x1ba" + bytes([alignment]))
		self.command(b"\x1bE" + bytes([1 if bold else 0]))
		lines = str(value or "").splitlines() or [""]
		for source in lines:
			wrapped = textwrap.wrap(source, width=self.width, break_long_words=True, replace_whitespace=False) or [""]
			for line in wrapped:
				self.command(line.encode(self.encoding, errors="replace") + b"\n")
		self.command(b"\x1bE\x00")

	def rule(self, char="-"):
		self.text(char * self.width)

	def pair(self, label: str, amount: str, *, bold: bool = False):
		amount = str(amount)
		available = max(1, self.width - len(amount) - 1)
		left = str(label)[:available]
		self.text(f"{left:<{available}} {amount}", bold=bold)

	def qr(self, value: str):
		data = (value or "").encode("ascii", errors="ignore")
		if not data:
			return
		self.command(b"\x1ba\x01")
		self.command(b"\x1d(k\x04\x001A2\x00")  # QR model 2
		self.command(b"\x1d(k\x03\x001C\x06")  # module size
		self.command(b"\x1d(k\x03\x001E0")  # error correction L
		length = len(data) + 3
		self.command(b"\x1d(k" + bytes([length & 0xFF, length >> 8]) + b"1P0" + data)
		self.command(b"\x1d(k\x03\x001Q0")
		self.command(b"\n")

	def finish(self) -> bytes:
		self.command(b"\n\n\n\x1dV\x00")
		return b"".join(self.parts)


def _money(value) -> str:
	return f"{frappe.utils.flt(value):.2f} грн"


def _xml_text(parent, path: str, default=""):
	element = parent.find(path) if parent is not None else None
	return element.text if element is not None and element.text is not None else default


def _fiscal_snapshot(receipt) -> dict:
	try:
		root = ET.fromstring(receipt.receipt_xml.encode("windows-1251"))
	except (AttributeError, UnicodeEncodeError, ET.ParseError) as exc:
		raise frappe.ValidationError(f"Фіскальний XML {receipt.name} неможливо надрукувати: {exc}") from exc
	head = root.find("CHECKHEAD")
	items = []
	for row in root.findall("./CHECKBODY/ROW"):
		items.append(
			{
				"name": _xml_text(row, "NAME"),
				"qty": _xml_text(row, "AMOUNT"),
				"price": _xml_text(row, "PRICE"),
				"amount": _xml_text(row, "COST"),
			}
		)
	payments = []
	for row in root.findall("./CHECKPAY/ROW"):
		payments.append({"name": _xml_text(row, "PAYFORMNM"), "amount": _xml_text(row, "SUM")})
	return {
		"seller": _xml_text(head, "ORGNM"),
		"tax_id": _xml_text(head, "TIN"),
		"point": _xml_text(head, "POINTNM"),
		"address": _xml_text(head, "POINTADDR"),
		"cashier": _xml_text(head, "CASHIER"),
		"date": _xml_text(head, "ORDERDATE"),
		"time": _xml_text(head, "ORDERTIME"),
		"items": items,
		"payments": payments,
		"total": _xml_text(root, "./CHECKTOTAL/SUM", str(receipt.total_amount or 0)),
	}


def render_order_receipt(order, printer, *, is_copy: bool = False) -> bytes:
	"""Build an immutable ESC/POS snapshot from POS Order and its fiscal XML."""
	if isinstance(order, str):
		order = frappe.get_doc("POS Order", order)
	receipt = frappe.get_doc("PRRO Receipt", order.prro_receipt) if order.prro_receipt else None
	if order.fiscal_mode == "Fiscal" and (
		not receipt or receipt.status not in {"Fiscalized", "Offline"} or not receipt.fiscal_number
	):
		frappe.throw("Фіскальний документ ще не підтверджено; друк заборонено")

	output = EscPosReceipt(
		width=printer.characters_per_line,
		encoding=printer.encoding,
		code_page=printer.code_page,
	)
	if is_copy:
		output.text("*** КОПІЯ ***", align="center", bold=True)
	output.text(
		"ФІСКАЛЬНИЙ ЧЕК" + (" · ОФЛАЙН" if receipt and receipt.is_offline else "")
		if receipt
		else "НЕФІСКАЛЬНИЙ ТОВАРНИЙ ЧЕК",
		align="center",
		bold=True,
	)

	if receipt:
		snapshot = _fiscal_snapshot(receipt)
		for value in (snapshot["seller"], f"РНОКПП/ЄДРПОУ: {snapshot['tax_id']}", snapshot["point"], snapshot["address"]):
			if value:
				output.text(value, align="center")
		output.text(f"Касир: {snapshot['cashier']}")
		items = snapshot["items"]
		payments = snapshot["payments"]
		total = snapshot["total"]
	else:
		desk = frappe.get_doc("POS Cash Desk", order.cash_desk)
		company = frappe.db.get_value("Company", desk.company, ["company_name", "tax_id"], as_dict=True) or {}
		output.text(company.get("company_name") or desk.company, align="center", bold=True)
		if company.get("tax_id"):
			output.text(f"ЄДРПОУ/РНОКПП: {company.tax_id}", align="center")
		output.text(desk.desk_name, align="center")
		items = [
			{"name": row.item_name or row.item_code, "qty": row.qty, "price": row.rate, "amount": row.amount}
			for row in order.items
		]
		payments = [
			{"name": row.mode_of_payment, "amount": row.amount}
			for row in order.payments_plan
			if row.status == "Confirmed"
		]
		total = order.grand_total

	output.rule()
	for item in items:
		output.text(item["name"])
		output.pair(f"{item['qty']} × {frappe.utils.flt(item['price']):.2f}", _money(item["amount"]))
	output.rule()
	output.pair("РАЗОМ", _money(total), bold=True)
	for payment in payments:
		output.pair(payment["name"], _money(payment["amount"]))
	if frappe.utils.flt(order.change_amount):
		output.pair("Решта", _money(order.change_amount))

	if receipt:
		output.rule()
		output.text(f"Фіскальний № {receipt.fiscal_number}", align="center", bold=True)
		output.text(f"Локальний № {receipt.local_number}", align="center")
		if receipt.qr_data:
			output.qr(receipt.qr_data)
	output.text(f"Чек {order.name}", align="center")
	output.text("Код для повернення:", align="center")
	output.text(order.lookup_token, align="center", bold=True)
	output.text(str(frappe.utils.now_datetime()), align="center")
	payload = output.finish()
	if len(payload) > MAX_PRINT_PAYLOAD:
		frappe.throw("Сформований чек перевищує ліміт друку 128 KiB")
	return payload


def render_fiscal_report(report: dict, printer, *, is_copy: bool = False) -> bytes:
	"""Render opening, X and Z shift reports as an ESC/POS thermal form."""
	output = EscPosReceipt(
		width=printer.characters_per_line,
		encoding=printer.encoding,
		code_page=printer.code_page,
	)
	if is_copy:
		output.text("*** КОПІЯ ***", align="center", bold=True)
	for value in (
		report.get("organization"),
		f"РНОКПП/ЄДРПОУ: {report.get('tax_id')}" if report.get("tax_id") else None,
		report.get("point_name"),
		report.get("point_address"),
	):
		if value:
			output.text(value, align="center")
	output.rule()
	output.text(report.get("title") or "ЗВІТ ПРРО", align="center", bold=True)
	if report.get("non_fiscal"):
		output.text("НЕФІСКАЛЬНИЙ", align="center", bold=True)
	if report.get("testing"):
		output.text("ТЕСТОВИЙ РЕЖИМ", align="center", bold=True)
	output.text(f"ПРРО: {report.get('cash_register_fiscal_number') or '—'}")
	output.text(f"Локальний № ПРРО: {report.get('cash_desk_local_number') or '—'}")
	output.text(f"Зміна: {report.get('shift') or '—'}")
	if report.get("operational_shift"):
		output.text(f"POS-зміна: {report['operational_shift']}")
	output.text(f"Касир: {report.get('cashier') or '—'}")
	output.text(f"Відкрито: {report.get('opened_at') or '—'}")

	if report.get("report_type") == "OPENING":
		output.rule()
		output.text("ЗМІНУ ВІДКРИТО", align="center", bold=True)
	else:
		output.rule()
		output.pair("Чеків", str(report.get("receipts_count") or 0))
		output.pair("Продажі", _money(report.get("sales_total")), bold=True)
		for payment in report.get("sales_payforms") or []:
			output.pair(f"  {payment.get('name') or payment.get('code')}", _money(payment.get("sum")))
		output.pair("Повернення", _money(report.get("returns_total")), bold=True)
		for payment in report.get("return_payforms") or []:
			output.pair(f"  {payment.get('name') or payment.get('code')}", _money(payment.get("sum")))
		output.pair("Чистий оборот", _money(report.get("net_total")), bold=True)
		output.pair("Службове внесення", _money(report.get("service_input")))
		output.pair("Службова видача", _money(report.get("service_output")))
		output.pair("Готівка в касі", _money(report.get("cash_balance")), bold=True)
		for tax in report.get("sales_taxes") or []:
			label = f"Податок {tax.get('letter') or tax.get('name') or ''} {frappe.utils.flt(tax.get('prc')):g}%"
			output.pair(label, _money(tax.get("sum")))
		if report.get("closed_at"):
			output.text(f"Закрито: {report['closed_at']}")

	if report.get("fiscal_number"):
		output.rule()
		output.text(f"Фіскальний № {report['fiscal_number']}", align="center", bold=True)
	if report.get("local_number"):
		output.text(f"Локальний № {report['local_number']}", align="center")
	if report.get("is_offline"):
		output.text("ОФЛАЙН", align="center", bold=True)
	output.text(f"Сформовано: {report.get('generated_at') or frappe.utils.now_datetime()}", align="center")
	payload = output.finish()
	if len(payload) > MAX_PRINT_PAYLOAD:
		frappe.throw("Сформований звіт перевищує ліміт друку 128 KiB")
	return payload


def queue_order_receipt(order, *, is_copy: bool = False, idem_key: str | None = None):
	if isinstance(order, str):
		order = frappe.get_doc("POS Order", order)
	desk = frappe.get_doc("POS Cash Desk", order.cash_desk)
	if not desk.receipt_printer:
		return None
	printer = frappe.get_doc("POS Printer", desk.receipt_printer)
	if printer.status == "Disabled":
		frappe.throw(f"Принтер {printer.name} вимкнено")
	key = idem_key or f"receipt:{order.name}:original"
	if is_copy:
		key = f"receipt:{order.name}:copy:{key}"
	existing = frappe.db.get_value("POS Print Job", {"idem_key": key}, "name")
	if existing:
		return frappe.get_doc("POS Print Job", existing)
	payload = render_order_receipt(order, printer, is_copy=is_copy)
	job = frappe.get_doc(
		{
			"doctype": "POS Print Job",
			"printer": printer.name,
			"cash_desk": desk.name,
			"job_type": "Copy" if is_copy else ("Fiscal Receipt" if order.prro_receipt else "Non Fiscal Receipt"),
			"reference_doctype": "POS Order",
			"reference_name": order.name,
			"format": "ESC/POS",
			"status": "Queued",
			"max_attempts": printer.max_attempts,
			"idem_key": key,
			"is_copy": 1 if is_copy else 0,
			"payload_base64": base64.b64encode(payload).decode(),
		}
	).insert(ignore_permissions=True)
	frappe.enqueue(
		"erpnext_ua.ua_pos.print_service.process_print_job",
		queue="short",
		enqueue_after_commit=True,
		job_name=f"pos-print-{job.name}",
		job_name_ref=job.name,
	)
	return job


def queue_fiscal_report(cash_desk: str, report: dict, *, idem_key: str):
	"""Create an immutable print job for a PRRO shift report snapshot."""
	desk = frappe.get_doc("POS Cash Desk", cash_desk)
	if not desk.receipt_printer:
		return None
	printer = frappe.get_doc("POS Printer", desk.receipt_printer)
	if printer.status == "Disabled":
		frappe.throw(f"Принтер {printer.name} вимкнено")
	key = f"fiscal-report:{str(report.get('report_type') or '').lower()}:{report.get('shift')}:{idem_key}"[:140]
	existing = frappe.db.get_value("POS Print Job", {"idem_key": key}, "name")
	if existing:
		return frappe.get_doc("POS Print Job", existing)
	payload = render_fiscal_report(report, printer)
	job = frappe.get_doc(
		{
			"doctype": "POS Print Job",
			"printer": printer.name,
			"cash_desk": desk.name,
			"job_type": "Report",
			"reference_doctype": "PRRO Shift",
			"reference_name": report["shift"],
			"format": "ESC/POS",
			"status": "Queued",
			"max_attempts": printer.max_attempts,
			"idem_key": key,
			"payload_base64": base64.b64encode(payload).decode(),
		}
	).insert(ignore_permissions=True)
	frappe.enqueue(
		"erpnext_ua.ua_pos.print_service.process_print_job",
		queue="short",
		enqueue_after_commit=True,
		job_name=f"pos-fiscal-report-{job.name}",
		job_name_ref=job.name,
	)
	return job


def _private_endpoint(host: str, port: int) -> tuple[str, int]:
	try:
		addresses = {
			row[4][0]
			for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
		}
	except OSError as exc:
		raise ConnectionError(f"DNS принтера {host} недоступний: {exc}") from exc
	if not addresses or any(not is_lan_address(address) for address in addresses):
		raise PermissionError("DNS принтера повернув не-LAN адресу; з'єднання заблоковано")
	return sorted(addresses)[0], port


def _mark_order_print_error(job, error: str):
	if job.reference_doctype == "POS Order" and not job.is_copy:
		frappe.db.set_value(
			"POS Order",
			job.reference_name,
			{"status": "Completed Print Error", "recovery_note": error[:500]},
			update_modified=False,
		)


def process_print_job(job_name_ref: str):
	frappe.db.sql("select name from `tabPOS Print Job` where name=%s for update", job_name_ref)
	job = frappe.get_doc("POS Print Job", job_name_ref)
	if job.status == "Done":
		return job.name
	if job.status not in {"Queued", "Failed"}:
		return job.name
	if job.next_retry_at and frappe.utils.get_datetime(job.next_retry_at) > frappe.utils.now_datetime():
		return job.name
	printer = frappe.get_doc("POS Printer", job.printer)
	attempts = int(job.attempts or 0) + 1
	frappe.db.set_value(
		"POS Print Job",
		job.name,
		{"status": "Printing", "attempts": attempts, "started_at": frappe.utils.now_datetime(), "error_message": None},
		update_modified=False,
	)
	frappe.db.commit()
	try:
		if printer.status == "Disabled":
			raise ConnectionError(f"Принтер {printer.name} вимкнено")
		payload = base64.b64decode(job.payload_base64, validate=True)
		endpoint = _private_endpoint(printer.host, int(printer.port))
		with socket.create_connection(endpoint, timeout=int(printer.connect_timeout or 5)) as connection:
			connection.settimeout(int(printer.connect_timeout or 5))
			connection.sendall(payload)
	except Exception as exc:
		error = str(exc)[:500]
		if attempts < int(job.max_attempts or 3):
			status = "Queued"
			next_retry = frappe.utils.add_to_date(
				frappe.utils.now_datetime(), minutes=min(15, 2 ** attempts), as_datetime=True
			)
		else:
			status = "Failed"
			next_retry = None
		frappe.db.set_value(
			"POS Print Job",
			job.name,
			{"status": status, "next_retry_at": next_retry, "error_message": error},
			update_modified=False,
		)
		frappe.db.set_value(
			"POS Printer", printer.name, {"status": "Error", "last_error": error}, update_modified=False
		)
		if status == "Failed":
			_mark_order_print_error(job, error)
		audit("print_error", {"cash_desk": job.cash_desk}, (job.doctype, job.name), reason=error)
		frappe.db.commit()
		return job.name

	printed_at = frappe.utils.now_datetime()
	frappe.db.set_value(
		"POS Print Job",
		job.name,
		{"status": "Done", "printed_at": printed_at, "next_retry_at": None, "error_message": None},
		update_modified=False,
	)
	frappe.db.set_value(
		"POS Printer",
		printer.name,
		{"status": "Active", "last_seen_at": printed_at, "last_error": None},
		update_modified=False,
	)
	if job.reference_doctype == "POS Order" and not job.is_copy:
		frappe.db.set_value(
			"POS Order", job.reference_name, {"status": "Completed", "recovery_note": None}, update_modified=False
		)
	audit("reprint" if job.is_copy else "print_done", {"cash_desk": job.cash_desk}, (job.doctype, job.name))
	frappe.db.commit()
	return job.name


def process_print_queue():
	"""Scheduler recovery: retries queued jobs; never blindly repeats an uncertain stale print."""
	stale_before = frappe.utils.add_to_date(
		frappe.utils.now_datetime(), minutes=-STALE_PRINT_MINUTES, as_datetime=True
	)
	for row in frappe.get_all(
		"POS Print Job",
		filters={"status": "Printing", "started_at": ("<=", stale_before)},
		fields=["name", "reference_doctype", "reference_name", "is_copy", "cash_desk"],
		limit=50,
	):
		error = "Невизначений результат друку після переривання worker; потрібен ручний повтор як КОПІЯ"
		frappe.db.set_value("POS Print Job", row.name, {"status": "Failed", "error_message": error}, update_modified=False)
		_mark_order_print_error(row, error)
	frappe.db.commit()

	for row in frappe.get_all(
		"POS Print Job", filters={"status": "Queued"}, fields=["name", "next_retry_at"], order_by="creation asc", limit=50
	):
		if row.next_retry_at and frappe.utils.get_datetime(row.next_retry_at) > frappe.utils.now_datetime():
			continue
		process_print_job(row.name)
