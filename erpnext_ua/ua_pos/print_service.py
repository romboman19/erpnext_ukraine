"""Durable server-side ESC/POS printing for network receipt printers."""

from __future__ import annotations

import base64
import html
import io
import socket
import textwrap
import xml.etree.ElementTree as ET
from datetime import datetime

import frappe

from erpnext_ua.ua_fiscal.payment import canonical_payform_name

from erpnext_ua.ua_pos.barcode import code128_svg_data_uri, encode_lookup_token
from erpnext_ua.ua_pos.doctype.pos_printer.pos_printer import is_lan_address
from erpnext_ua.ua_pos.services.common import audit


MAX_PRINT_PAYLOAD = 128 * 1024
STALE_PRINT_MINUTES = 10
PRRO_SOFTWARE_PRODUCT = "ПРРО ERPNext Україна"


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

	def barcode_code128(self, value: str):
		data = str(value or "").encode("ascii", errors="strict")
		if not data or len(data) > 250:
			return
		payload = b"{B" + data
		self.command(b"\x1ba\x01\x1dH\x02\x1dh\x50\x1dw\x02")
		self.command(b"\x1dkI" + bytes([len(payload)]) + payload + b"\n")

	def finish(self) -> bytes:
		self.command(b"\n\n\n\x1dV\x00")
		return b"".join(self.parts)


def _money(value) -> str:
	return f"{frappe.utils.flt(value):.2f} грн"


def _xml_text(parent, path: str, default=""):
	element = parent.find(path) if parent is not None else None
	return element.text if element is not None and element.text is not None else default


def _receipt_datetime(order_date: str, order_time: str) -> datetime:
	try:
		return datetime.strptime(f"{order_date}{order_time}", "%d%m%Y%H%M%S")
	except (TypeError, ValueError) as exc:
		raise frappe.ValidationError("У фіскальному XML відсутня коректна дата або час операції") from exc


def _qr_svg_data_uri(value: str) -> str:
	"""Render QR locally; no receipt data is sent to a third-party service."""
	try:
		import qrcode
		import qrcode.image.svg
	except ImportError as exc:
		raise frappe.ValidationError("Не встановлено залежність qrcode для друку фіскального чека") from exc
	image = qrcode.make(
		value,
		image_factory=qrcode.image.svg.SvgPathImage,
		box_size=4,
		border=2,
	)
	buffer = io.BytesIO()
	image.save(buffer)
	return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def fiscal_snapshot(receipt, *, include_qr_image: bool = False) -> dict:
	"""Parse the immutable XML that was signed and accepted by the fiscal server."""
	if isinstance(receipt, str):
		receipt = frappe.get_doc("PRRO Receipt", receipt)
	try:
		root = ET.fromstring(receipt.receipt_xml.encode("windows-1251"))
	except (AttributeError, UnicodeEncodeError, ET.ParseError) as exc:
		raise frappe.ValidationError(f"Фіскальний XML {receipt.name} неможливо надрукувати: {exc}") from exc
	head = root.find("CHECKHEAD")
	items = []
	for row in root.findall("./CHECKBODY/ROW"):
		excise_labels = [_xml_text(label, "EXCISELABEL") for label in row.findall("./EXCISELABELS/ROW")]
		items.append(
			{
				"code": _xml_text(row, "CODE"),
				"barcode": _xml_text(row, "BARCODE"),
				"uktzed": _xml_text(row, "UKTZED"),
				"dkpp": _xml_text(row, "DKPP"),
				"name": _xml_text(row, "NAME"),
				"description": _xml_text(row, "DESCRIPTION"),
				"uom": _xml_text(row, "UNITNM"),
				"qty": _xml_text(row, "AMOUNT"),
				"price": _xml_text(row, "PRICE"),
				"letters": _xml_text(row, "LETTERS"),
				"amount": _xml_text(row, "COST"),
				"excise_labels": [value for value in excise_labels if value],
				"tobacco_weight": _xml_text(row, "TOBACCOWEIGHT"),
				"tobacco_qty": _xml_text(row, "TOBACCOQT"),
				"alcohol_strength": _xml_text(row, "ALCOSTRENGTH"),
				"alcohol_volume": _xml_text(row, "ALCOVOL"),
			}
		)
	payments = []
	for row in root.findall("./CHECKPAY/ROW"):
		code = int(_xml_text(row, "PAYFORMCD", "0") or 0)
		payment_name = canonical_payform_name(code, _xml_text(row, "PAYFORMNM"))
		paysys = []
		for payment_system in row.findall("./PAYSYS/ROW"):
			paysys.append(
				{
					"tax_number": _xml_text(payment_system, "TAXNUM"),
					"name": _xml_text(payment_system, "NAME"),
					"acquirer_id": _xml_text(payment_system, "ACQUIREID"),
					"acquirer_tax_number": _xml_text(payment_system, "ACQUIREPN"),
					"acquirer_name": _xml_text(payment_system, "ACQUIRENM"),
					"transaction_id": _xml_text(payment_system, "ACQUIRETRANSID"),
					"transaction_date": _xml_text(payment_system, "POSTRANSDATE"),
					"transaction_number": _xml_text(payment_system, "POSTRANSNUM"),
					"device_id": _xml_text(payment_system, "DEVICEID"),
					"epz_details": _xml_text(payment_system, "EPZDETAILS"),
					"auth_code": _xml_text(payment_system, "AUTHCD"),
					"sum": _xml_text(payment_system, "SUM"),
					"commission": _xml_text(payment_system, "COMMISSION"),
				}
			)
		payments.append(
			{
				"code": code,
				"form": "ГОТІВКА" if code == 0 else ("БЕЗГОТІВКОВА" if code == 1 else "ІНШЕ"),
				"means": payment_name,
				"amount": _xml_text(row, "SUM"),
				"provided": _xml_text(row, "PROVIDED"),
				"change": _xml_text(row, "REMAINS"),
				"currency": "UAH",
				"paysys": paysys,
			}
		)
	taxes = []
	for row in root.findall("./CHECKTAX/ROW"):
		taxes.append(
			{
				"type": int(_xml_text(row, "TYPE", "0") or 0),
				"name": _xml_text(row, "NAME"),
				"letter": _xml_text(row, "LETTER"),
				"rate": _xml_text(row, "PRC"),
				"amount": _xml_text(row, "SUM"),
			}
		)
	order_date = _xml_text(head, "ORDERDATE")
	order_time = _xml_text(head, "ORDERTIME")
	dt = _receipt_datetime(order_date, order_time)
	register_number = _xml_text(head, "CASHREGISTERNUM")
	total = _xml_text(root, "./CHECKTOTAL/SUM", str(receipt.total_amount or 0))
	is_offline = bool(getattr(receipt, "is_offline", 0)) or _xml_text(head, "OFFLINE").lower() == "true"
	from erpnext_ua.ua_fiscal.receipt_format import build_verification_url, offline_control_number

	qr_data = build_verification_url(
		register_number,
		receipt.fiscal_number,
		total,
		dt,
		mac=getattr(receipt, "signed_document_hash", None) if is_offline else None,
	)
	snapshot = {
		"seller": _xml_text(head, "ORGNM"),
		"tax_id": _xml_text(head, "TIN"),
		"vat_number": _xml_text(head, "IPN"),
		"tax_prefix": "ПН" if _xml_text(head, "IPN") else "ІД",
		"tax_number": _xml_text(head, "IPN") or _xml_text(head, "TIN"),
		"point": _xml_text(head, "POINTNM"),
		"address": _xml_text(head, "POINTADDR"),
		"cashier": _xml_text(head, "CASHIER"),
		"date": dt.strftime("%d.%m.%Y"),
		"time": dt.strftime("%H:%M:%S"),
		"register_number": register_number,
		"operation": "ПОВЕРНЕННЯ" if _xml_text(head, "DOCSUBTYPE") == "1" else "ПРОДАЖ",
		"title": "ВИДАТКОВИЙ ЧЕК" if _xml_text(head, "DOCSUBTYPE") == "1" else "ФІСКАЛЬНИЙ ЧЕК",
		"mode": "ОФЛАЙН" if is_offline else "ОНЛАЙН",
		"offline_control_number": offline_control_number(receipt.fiscal_number) if is_offline else "",
		"testing": _xml_text(head, "TESTING").lower() == "true",
		"items": items,
		"payments": payments,
		"taxes": taxes,
		"total": total,
		"rounding": _xml_text(root, "./CHECKTOTAL/RNDSUM"),
		"before_rounding": _xml_text(root, "./CHECKTOTAL/NORNDSUM"),
		"change": next((payment["change"] for payment in payments if payment["change"]), "0.00"),
		"fiscal_number": receipt.fiscal_number,
		"local_number": receipt.local_number,
		"qr_data": qr_data,
		"software_product": PRRO_SOFTWARE_PRODUCT,
	}
	if include_qr_image:
		snapshot["qr_svg"] = _qr_svg_data_uri(qr_data)
	return snapshot


def render_browser_fiscal_receipt(snapshot: dict, *, lookup_token: str | None = None) -> str:
	"""Render a safe browser/print preview from the immutable fiscal snapshot."""
	def esc(value) -> str:
		return html.escape(str(value or ""), quote=True)

	def money(value) -> str:
		return f"{frappe.utils.flt(value):.2f}"

	item_rows = []
	for row in snapshot.get("items") or []:
		codes = []
		if row.get("uktzed"):
			codes.append(f"УКТ ЗЕД {esc(row['uktzed'])}")
		if row.get("dkpp"):
			codes.append(f"ДКПП {esc(row['dkpp'])}")
		if row.get("barcode"):
			codes.append(f"Штрихкод {esc(row['barcode'])}")
		codes.extend(f"Акцизна марка {esc(value)}" for value in row.get("excise_labels") or [])
		if row.get("tobacco_qty"):
			codes.append(f"Кількість тютюнових виробів в одиниці: {esc(row['tobacco_qty'])}")
		if row.get("tobacco_weight"):
			codes.append(f"Вага одиниці тютюнового виробу: {esc(row['tobacco_weight'])}")
		if row.get("alcohol_volume"):
			codes.append(f"Об’єм алкогольного напою: {esc(row['alcohol_volume'])} л")
		if row.get("alcohol_strength"):
			codes.append(f"Міцність алкогольного напою: {esc(row['alcohol_strength'])}%")
		code_html = f"<br><small>{'<br>'.join(codes)}</small>" if codes else ""
		description = f"<br>{esc(row['description'])}" if row.get("description") else ""
		item_rows.append(
			"<tr><td>"
			f"<b>{esc(row.get('name'))}</b>{description}{code_html}<br>"
			f"{esc(row.get('qty'))} {esc(row.get('uom'))} × {money(row.get('price'))}"
			f"</td><td>{money(row.get('amount'))} UAH {esc(row.get('letters'))}</td></tr>"
		)

	tax_rows = []
	for row in snapshot.get("taxes") or []:
		label = "ПДВ" if int(row.get("type") or 0) == 0 else (row.get("name") or "ПОДАТОК")
		tax_rows.append(
			f"<tr><td>{esc(label)} {esc(row.get('letter'))} {money(row.get('rate'))}%</td>"
			f"<td>{money(row.get('amount'))} UAH</td></tr>"
		)

	is_return = snapshot.get("operation") == "ПОВЕРНЕННЯ"
	payment_rows = []
	for row in snapshot.get("payments") or []:
		means = ""
		if row.get("means") and str(row["means"]).upper() != str(row.get("form") or "").upper():
			means = f"<br><small>Засіб оплати: {esc(row['means'])}</small>"
		details = []
		if not is_return and row.get("provided") not in (None, ""):
			details.append(f"ОТРИМАНО: {money(row['provided'])} {esc(row.get('currency'))}")
		if not is_return and row.get("change") not in (None, ""):
			details.append(f"РЕШТА: {money(row['change'])} {esc(row.get('currency'))}")
		for payment_system in row.get("paysys") or []:
			merchant = payment_system.get("name") or payment_system.get("tax_number")
			acquirer = payment_system.get("acquirer_name") or payment_system.get("acquirer_id")
			if merchant:
				details.append(f"Торговець: {esc(merchant)}")
			if acquirer:
				details.append(f"Еквайр: {esc(acquirer)}")
			if payment_system.get("device_id"):
				details.append(f"Платіжний пристрій: {esc(payment_system['device_id'])}")
			if payment_system.get("epz_details"):
				details.append(f"ЕПЗ: {esc(payment_system['epz_details'])}")
			if payment_system.get("auth_code"):
				details.append(f"Код авторизації: {esc(payment_system['auth_code'])}")
			if payment_system.get("transaction_number"):
				details.append(f"Номер операції: {esc(payment_system['transaction_number'])}")
			if payment_system.get("transaction_id"):
				details.append(f"Ідентифікатор операції: {esc(payment_system['transaction_id'])}")
			if payment_system.get("commission"):
				details.append(f"Комісія: {money(payment_system['commission'])} {esc(row.get('currency'))}")
			details.append(f"Вид операції: {esc(snapshot.get('operation'))}")
		detail_html = f"<br><small>{'<br>'.join(details)}</small>" if details else ""
		payment_rows.append(
			f"<tr><td>{esc(row.get('form'))}{means}{detail_html}</td>"
			f"<td>{money(row.get('amount'))} {esc(row.get('currency'))}</td></tr>"
		)

	qr = ""
	if snapshot.get("qr_svg"):
		qr = (
			'<div class="fiscal-center fiscal-qr">'
			f'<img src="{esc(snapshot["qr_svg"])}" alt="QR перевірки чека">'
			f'<div class="fiscal-muted fiscal-url">{esc(snapshot.get("qr_data"))}</div></div>'
		)
	rounding = ""
	if frappe.utils.flt(snapshot.get("rounding")):
		rounding = f"<tr><td>Заокруглення</td><td>{money(snapshot['rounding'])} UAH</td></tr>"
	offline_control = ""
	if snapshot.get("offline_control_number"):
		offline_control = f"<br>Контрольне число: {esc(snapshot['offline_control_number'])}"
	lookup = ""
	if lookup_token:
		lookup_barcode = encode_lookup_token(lookup_token)
		lookup = (
			'<p class="fiscal-center fiscal-muted">Код чека для повернення:<br>'
			'<span class="fiscal-barcode">'
			f'<img src="{esc(code128_svg_data_uri(lookup_barcode))}" alt="Штрихкод повернення">'
			f"</span><br><b>{esc(lookup_barcode)}</b></p>"
		)

	summary_rows = (
		f'<tr><td><b>СУМА</b></td><td><b>{money(snapshot.get("total"))} UAH</b></td></tr>'
		if is_return
		else f'<tr><td><b>УСЬОГО</b></td><td><b>{money(snapshot.get("total"))} UAH</b></td></tr>'
	)
	summary_rows += "".join(tax_rows)
	if not is_return:
		summary_rows += rounding
		summary_rows += (
			f'<tr><td><b>ДО СПЛАТИ</b></td><td><b>{money(snapshot.get("total"))} UAH</b></td></tr>'
		)
	software_product = f'<br><b>{esc(snapshot.get("software_product"))}</b>'

	return (
		'<div class="fiscal-receipt">'
		f'<div class="fiscal-center"><b>{esc(snapshot.get("seller"))}</b><br>'
		f'{esc(snapshot.get("point"))}<br>{esc(snapshot.get("address"))}<br>'
		f'{esc(snapshot.get("tax_prefix"))} {esc(snapshot.get("tax_number"))}<br>'
		f'<span class="fiscal-muted">Касир: {esc(snapshot.get("cashier"))}</span></div>'
		+ ('<p class="fiscal-center"><b>ТЕСТОВИЙ РЕЖИМ</b></p>' if snapshot.get("testing") else "")
		+ f'<table class="fiscal-table">{"".join(item_rows)}</table>'
		+ f'<table class="fiscal-table">{summary_rows}</table>'
		+ f'<table class="fiscal-table">{"".join(payment_rows)}</table>'
		+ f'<p class="fiscal-center"><b>ЧЕК № {esc(snapshot.get("fiscal_number"))}</b><br>'
		+ f'Локальний № {esc(snapshot.get("local_number"))}<br>'
		+ f'{esc(snapshot.get("date"))} {esc(snapshot.get("time"))}</p>'
		+ qr
		+ f'<p class="fiscal-center"><b>{esc(snapshot.get("mode"))}</b>{offline_control}<br>'
		+ f'ФН ПРРО {esc(snapshot.get("register_number"))}<br><b>{esc(snapshot.get("title"))}</b>'
		+ software_product
		+ "</p>"
		+ lookup
		+ "</div>"
	)


# Backward-compatible private name used by older callers/tests.
_fiscal_snapshot = fiscal_snapshot


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

	if receipt:
		snapshot = fiscal_snapshot(receipt)
		for value in (
			snapshot["seller"],
			snapshot["point"],
			snapshot["address"],
			f"{snapshot['tax_prefix']} {snapshot['tax_number']}",
		):
			if value:
				output.text(value, align="center")
		output.text(f"Касир: {snapshot['cashier']}")
		items = snapshot["items"]
		payments = snapshot["payments"]
		total = snapshot["total"]
	else:
		output.text("НЕФІСКАЛЬНИЙ ТОВАРНИЙ ЧЕК", align="center", bold=True)
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
		if item.get("description"):
			output.text(item["description"])
		if item.get("uktzed"):
			output.text(f"УКТ ЗЕД {item['uktzed']}")
		elif item.get("dkpp"):
			output.text(f"ДКПП {item['dkpp']}")
		if item.get("barcode"):
			output.text(f"Штрихкод {item['barcode']}")
		for label in item.get("excise_labels") or []:
			output.text(f"Акцизна марка {label}")
		if item.get("tobacco_qty"):
			output.text(f"Кількість тютюнових виробів в одиниці: {item['tobacco_qty']}")
		if item.get("tobacco_weight"):
			output.text(f"Вага одиниці тютюнового виробу: {item['tobacco_weight']}")
		if item.get("alcohol_volume"):
			output.text(f"Об’єм алкогольного напою: {item['alcohol_volume']} л")
		if item.get("alcohol_strength"):
			output.text(f"Міцність алкогольного напою: {item['alcohol_strength']}%")
		amount = _money(item["amount"])
		if item.get("letters"):
			amount += f" {item['letters']}"
		output.pair(
			f"{item['qty']} {item.get('uom') or ''} × {frappe.utils.flt(item['price']):.2f}",
			amount,
		)
	output.rule()
	is_return = bool(receipt and snapshot["operation"] == "ПОВЕРНЕННЯ")
	output.pair("СУМА" if is_return else "УСЬОГО", f"{frappe.utils.flt(total):.2f} UAH", bold=True)
	if receipt:
		for tax in snapshot["taxes"]:
			label = "ПДВ" if tax["type"] == 0 else (tax["name"] or "ПОДАТОК")
			output.pair(
				f"{label} {tax['letter']} {frappe.utils.flt(tax['rate']):g}%".strip(),
				f"{frappe.utils.flt(tax['amount']):.2f} UAH",
			)
		if snapshot["rounding"] and not is_return:
			output.pair("Заокруглення", f"{frappe.utils.flt(snapshot['rounding']):.2f} UAH")
		if not is_return:
			output.pair("ДО СПЛАТИ", f"{frappe.utils.flt(total):.2f} UAH", bold=True)
		for payment in payments:
			output.pair(payment["form"], f"{frappe.utils.flt(payment['amount']):.2f} {payment['currency']}")
			if payment["means"] and payment["means"].upper() != payment["form"]:
				output.text(f"Засіб оплати: {payment['means']}")
			if not is_return and payment["provided"]:
				output.pair("ОТРИМАНО", f"{frappe.utils.flt(payment['provided']):.2f} {payment['currency']}")
			for payment_system in payment["paysys"]:
				merchant = payment_system["name"] or payment_system["tax_number"]
				acquirer = payment_system["acquirer_name"] or payment_system["acquirer_id"]
				if merchant:
					output.text(f"Торговець: {merchant}")
				if acquirer:
					output.text(f"Еквайр: {acquirer}")
				if payment_system["device_id"]:
					output.text(f"Платіжний пристрій: {payment_system['device_id']}")
				if payment_system["commission"]:
					output.text(f"Комісія: {frappe.utils.flt(payment_system['commission']):.2f} UAH")
				output.text(f"Вид операції: {snapshot['operation']}")
				if payment_system["epz_details"]:
					output.text(f"ЕПЗ {payment_system['epz_details']}")
				payment_details = " ".join(
					value
					for value in (
						payment_system["auth_code"],
						payment_system["transaction_number"],
						payment_system["transaction_id"],
					)
					if value
				)
				if payment_details:
					output.text(f"ПЛАТІЖНА СИСТЕМА {payment_details}")
		if not is_return and any(payment["code"] == 0 for payment in payments):
			output.pair("РЕШТА", f"{frappe.utils.flt(snapshot['change']):.2f} UAH")
	else:
		for payment in payments:
			output.pair(payment["name"], _money(payment["amount"]))
		if frappe.utils.flt(order.change_amount):
			output.pair("Решта", _money(order.change_amount))

	if receipt:
		output.rule()
		output.text(f"ЧЕК № {receipt.fiscal_number}", align="center", bold=True)
		output.text(f"Локальний № {receipt.local_number}", align="center")
		output.text(f"{snapshot['date']} {snapshot['time']}", align="center")
		if snapshot["qr_data"]:
			output.qr(snapshot["qr_data"])
		output.text(snapshot["mode"], align="center", bold=True)
		if snapshot["offline_control_number"]:
			output.text(f"Контрольне число: {snapshot['offline_control_number']}", align="center")
		output.text(f"ФН ПРРО {snapshot['register_number']}", align="center")
		output.text(snapshot["title"], align="center", bold=True)
		output.text(snapshot["software_product"], align="center", bold=True)
	output.text(f"Чек {order.name}", align="center")
	output.text("Код для повернення:", align="center")
	lookup_barcode = encode_lookup_token(order.lookup_token)
	output.barcode_code128(lookup_barcode)
	output.text(lookup_barcode, align="center", bold=True)
	if not receipt:
		output.text(str(frappe.utils.now_datetime()), align="center")
	payload = output.finish()
	if len(payload) > MAX_PRINT_PAYLOAD:
		frappe.throw("Сформований чек перевищує ліміт друку 128 KiB")
	return payload


def render_browser_fiscal_report(report: dict) -> str:
	"""Render an opening/X/Z report for Desk preview and browser printing."""
	def esc(value) -> str:
		return html.escape(str(value or ""), quote=True)

	def money(value) -> str:
		return f"{frappe.utils.flt(value):.2f} грн"

	def payment_rows(label: str, rows: list[dict]) -> str:
		return "".join(
			f"<tr><td>{esc(label)} · {esc(row.get('name') or row.get('code'))}</td>"
			f"<td>{money(row.get('sum'))}</td></tr>"
			for row in rows or []
		)

	tax_rows = "".join(
		f"<tr><td>Податок продажу {esc(row.get('letter') or row.get('name'))} "
		f"{frappe.utils.flt(row.get('prc')):g}%</td><td>{money(row.get('sum'))}</td></tr>"
		for row in report.get("sales_taxes") or []
	)
	tax_rows += "".join(
		f"<tr><td>Податок повернення {esc(row.get('letter') or row.get('name'))} "
		f"{frappe.utils.flt(row.get('prc')):g}%</td><td>{money(row.get('sum'))}</td></tr>"
		for row in report.get("return_taxes") or []
	)
	totals = ""
	if report.get("report_type") != "OPENING":
		totals = (
			'<div class="fiscal-rule"></div><table class="fiscal-table">'
			f'<tr><td>Чеків продажу</td><td>{int(report.get("sales_receipts_count") or 0)}</td></tr>'
			f'<tr><td><b>Продажі</b></td><td><b>{money(report.get("sales_total"))}</b></td></tr>'
			+ payment_rows("Продаж", report.get("sales_payforms") or [])
			+ f'<tr><td>Чеків повернення</td><td>{int(report.get("return_receipts_count") or 0)}</td></tr>'
			+ f'<tr><td><b>Повернення</b></td><td><b>{money(report.get("returns_total"))}</b></td></tr>'
			+ payment_rows("Повернення", report.get("return_payforms") or [])
			+ f'<tr><td><b>Чистий оборот</b></td><td><b>{money(report.get("net_total"))}</b></td></tr>'
			+ f'<tr><td>Службове внесення</td><td>{money(report.get("service_input"))}</td></tr>'
			+ f'<tr><td>Службова видача</td><td>{money(report.get("service_output"))}</td></tr>'
			+ f'<tr><td><b>Розрахунковий залишок</b></td><td><b>{money(report.get("cash_balance"))}</b></td></tr>'
			+ tax_rows
			+ "</table>"
		)
	opening = (
		'<div class="fiscal-center"><b>ЗМІНУ ВІДКРИТО</b></div>'
		if report.get("report_type") == "OPENING"
		else ""
	)
	fiscal_number = ""
	if report.get("fiscal_number"):
		fiscal_number = (
			'<div class="fiscal-rule"></div><div class="fiscal-center">'
			f'<b>{esc(report.get("fiscal_number_label") or "Фіскальний №")} '
			f'{esc(report.get("fiscal_number"))}</b><br>'
			f'Локальний № документа {esc(report.get("local_number"))}</div>'
		)
	return (
		'<div class="fiscal-form">'
		f'<div class="fiscal-center"><b>{esc(report.get("organization"))}</b><br>'
		f'{esc(report.get("point_name"))}<br>{esc(report.get("point_address"))}<br>'
		f'{esc(report.get("tax_prefix") or "ІД")} {esc(report.get("tax_number") or report.get("tax_id"))}</div>'
		'<div class="fiscal-rule"></div>'
		f'<div class="fiscal-center fiscal-title"><b>{esc(report.get("title"))}</b></div>'
		+ ('<div class="fiscal-center"><b>НЕФІСКАЛЬНИЙ</b></div>' if report.get("non_fiscal") else "")
		+ ('<div class="fiscal-center"><b>ТЕСТОВИЙ РЕЖИМ</b></div>' if report.get("testing") else "")
		+ f'<p>ФН ПРРО {esc(report.get("cash_register_fiscal_number") or "—")}<br>'
		+ f'Локальний № ПРРО: {esc(report.get("cash_desk_local_number") or "—")}<br>'
		+ f'Фіскальна зміна: {esc(report.get("shift") or "—")}<br>'
		+ f'Касир: {esc(report.get("cashier") or "—")}<br>'
		+ f'Відкрито: {esc(report.get("opened_at") or "—")}'
		+ (f'<br>Закрито: {esc(report.get("closed_at"))}' if report.get("closed_at") else "")
		+ (f'<br>Z-документ: {esc(report.get("document_at"))}' if report.get("document_at") else "")
		+ "</p>"
		+ opening
		+ totals
		+ fiscal_number
		+ ('<div class="fiscal-center"><b>ОФЛАЙН</b></div>' if report.get("is_offline") else "")
		+ f'<p class="fiscal-center fiscal-muted">Надруковано: {esc(report.get("generated_at"))}<br>'
		+ f'<b>{esc(PRRO_SOFTWARE_PRODUCT)}</b></p></div>'
	)


def render_fiscal_report(report: dict, printer, *, is_copy: bool = False) -> bytes:
	"""Render opening, X and Z shift reports as an ESC/POS thermal form."""
	output = EscPosReceipt(
		width=printer.characters_per_line,
		encoding=printer.encoding,
		code_page=printer.code_page,
	)
	if is_copy:
		output.text("*** КОПІЯ ***", align="center", bold=True)
	tax_number = report.get("tax_number") or report.get("tax_id")
	for value in (
		report.get("organization"),
		f"{report.get('tax_prefix') or 'ІД'} {tax_number}" if tax_number else None,
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
	output.text(f"ФН ПРРО {report.get('cash_register_fiscal_number') or '—'}")
	output.text(f"Локальний № ПРРО: {report.get('cash_desk_local_number') or '—'}")
	output.text(f"Фіскальна зміна: {report.get('shift') or '—'}")
	if report.get("operational_shift"):
		output.text(f"Управлінська зміна: {report['operational_shift']}")
	output.text(f"Касир: {report.get('cashier') or '—'}")
	output.text(f"Відкрито: {report.get('opened_at') or '—'}")

	if report.get("report_type") == "OPENING":
		output.rule()
		output.text("ЗМІНУ ВІДКРИТО", align="center", bold=True)
	else:
		output.rule()
		output.pair("Чеків продажу", str(report.get("sales_receipts_count") or 0))
		output.pair("Продажі", _money(report.get("sales_total")), bold=True)
		for payment in report.get("sales_payforms") or []:
			output.pair(f"  {payment.get('name') or payment.get('code')}", _money(payment.get("sum")))
		output.pair("Чеків повернення", str(report.get("return_receipts_count") or 0))
		output.pair("Повернення", _money(report.get("returns_total")), bold=True)
		for payment in report.get("return_payforms") or []:
			output.pair(f"  {payment.get('name') or payment.get('code')}", _money(payment.get("sum")))
		output.pair("Чистий оборот", _money(report.get("net_total")), bold=True)
		output.pair("Службове внесення", _money(report.get("service_input")))
		output.pair("Службова видача", _money(report.get("service_output")))
		output.pair("Розрахунковий залишок", _money(report.get("cash_balance")), bold=True)
		for tax in report.get("sales_taxes") or []:
			label = f"Податок {tax.get('letter') or tax.get('name') or ''} {frappe.utils.flt(tax.get('prc')):g}%"
			output.pair(label, _money(tax.get("sum")))
		for tax in report.get("return_taxes") or []:
			label = f"Податок повернення {tax.get('letter') or tax.get('name') or ''} {frappe.utils.flt(tax.get('prc')):g}%"
			output.pair(label, _money(tax.get("sum")))
		if report.get("closed_at"):
			output.text(f"Закрито: {report['closed_at']}")
		if report.get("document_at"):
			output.text(f"Z-документ: {report['document_at']}")

	if report.get("fiscal_number"):
		output.rule()
		output.text(
			f"{report.get('fiscal_number_label') or 'Фіскальний №'} {report['fiscal_number']}",
			align="center",
			bold=True,
		)
	if report.get("local_number"):
		output.text(f"Локальний № документа {report['local_number']}", align="center")
	if report.get("is_offline"):
		output.text("ОФЛАЙН", align="center", bold=True)
	output.text(f"Надруковано: {report.get('generated_at') or frappe.utils.now_datetime()}", align="center")
	output.text(PRRO_SOFTWARE_PRODUCT, align="center", bold=True)
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
