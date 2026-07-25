"""Генерація XML документів check01 і zrep01 для фіскального сервера ДПС (ЄВПЕЗ).

Кодування — windows-1251 (вимога опису АРІ). Порядок елементів строго за
check01.xsd / zrep01.xsd (дзеркало: /home/romboman19/prro_docs), інакше сервер
відхилить документ з ERROR_XML.

Для ФОП без ПДВ (група 2, група 3 5%) секції LETTERS/CHECKTAX не формуються.
Для ФОП 3 зі ставкою 3% додається CHECKTAX і LETTERS у рядках (передається явно).
"""

import os
import uuid
from functools import lru_cache
from xml.sax.saxutils import escape

import frappe

_SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "schemas")
_SCHEMA_BY_ROOT = {"CHECK": "check01.xsd", "ZREP": "zrep01.xsd", "TICKET": "ticket01.xsd"}


@lru_cache(maxsize=3)
def _schema(filename: str):
	from lxml import etree

	return etree.XMLSchema(etree.parse(os.path.join(_SCHEMA_DIR, filename)))


def validate_document(xml: bytes) -> None:
	"""Валідує документ проти офіційної XSD ДПС; кидає ValueError при невідповідності.

	Викликається перед підписом/відправкою, щоб зловити ERROR_XML локально.
	"""
	from lxml import etree

	doc = etree.fromstring(xml)
	filename = _SCHEMA_BY_ROOT.get(doc.tag)
	if not filename:
		return
	schema = _schema(filename)
	if not schema.validate(doc):
		raise ValueError(f"XML не відповідає {filename}: {schema.error_log}")

# DOCTYPE (CheckDocumentType)
DOCTYPE_SALE = 0
DOCTYPE_OPEN_SHIFT = 100
DOCTYPE_CLOSE_SHIFT = 101
DOCTYPE_OFFLINE_BEGIN = 102
DOCTYPE_OFFLINE_END = 103

# DOCSUBTYPE (CheckDocumentSubType)
SUBTYPE_GOODS = 0
SUBTYPE_RETURN = 1
SUBTYPE_SERVICE_DEPOSIT = 2
SUBTYPE_SERVICE_ISSUE = 4
SUBTYPE_STORNO = 5

# Порядок елементів CHECKHEAD (CHead) за check01.xsd
_CHECKHEAD_ORDER = [
	"DOCTYPE", "DOCSUBTYPE", "UID", "TIN", "IPN", "ORGNM", "POINTNM", "POINTADDR",
	"ORDERDATE", "ORDERTIME", "ORDERNUM", "CASHDESKNUM", "CASHREGISTERNUM",
	"ORDERRETCASHREGNUM", "ORDERRETDATE", "ORDERRETNUM", "ORDERSTORNUM", "OPERTYPENM",
	"VEHICLERN", "REVOKELASTONLINEDOC", "CASHIER", "LOGOURL", "COMMENT", "VER",
	"ORDERTAXNUM", "OFFLINE", "PREVDOCHASH", "REVOKED", "STORNED", "TESTING",
]

# Порядок елементів ZREPHEAD (ZHead) за zrep01.xsd
_ZREPHEAD_ORDER = [
	"UID", "TIN", "IPN", "ORGNM", "POINTNM", "POINTADDR", "ORDERDATE", "ORDERTIME",
	"ORDERNUM", "CASHDESKNUM", "CASHREGISTERNUM", "CASHIER", "VER", "ORDERTAXNUM",
	"OFFLINE", "PREVDOCHASH", "TESTING",
]


def _fmt_sum(value) -> str:
	return f"{frappe.utils.flt(value):.2f}"


def _ordered(head: dict, order: list[str]) -> str:
	rows = []
	for tag in order:
		val = head.get(tag)
		if val is None or val == "":
			continue
		rows.append(f"<{tag}>{escape(str(val))}</{tag}>")
	return "".join(rows)


def _common_head(fop: dict, register: dict, local_number, cashier_name, dt) -> dict:
	registered_name = (fop.get("prro_registered_name") or fop.get("fop_full_name") or "").strip()
	head = {
		"UID": str(uuid.uuid4()).upper(),
		"TIN": fop["tax_id"],
		# ДПС звіряє ORGNM буквально з реєстраційними даними. Не можна
		# самовільно додавати «ФОП», змінювати регістр або інші символи.
		"ORGNM": registered_name,
		"POINTNM": register["unit_name"],
		"POINTADDR": register["unit_address"],
		"ORDERDATE": dt.strftime("%d%m%Y"),
		"ORDERTIME": dt.strftime("%H%M%S"),
		"ORDERNUM": local_number,
		"CASHDESKNUM": register.get("local_number") or 1,
		"CASHREGISTERNUM": register["fiscal_number"],
		"CASHIER": cashier_name,
		"VER": 1,
	}
	if fop.get("vat_payer") and fop.get("vat_number"):
		head["IPN"] = fop["vat_number"]
	return head


def build_check_head(
	*,
	doctype: int,
	fop: dict,
	register: dict,
	local_number: int,
	cashier_name: str,
	posting_datetime=None,
	subtype: int | None = None,
	testing: bool = False,
	offline: bool = False,
	prev_doc_hash: str | None = None,
	order_tax_num: str | None = None,
	order_ret_num: str | None = None,
	order_ret_cash_register: str | None = None,
	order_ret_date: str | None = None,
	order_storno_num: str | None = None,
	revoke_last_online_document: bool = False,
) -> dict:
	dt = posting_datetime or frappe.utils.now_datetime()
	head = _common_head(fop, register, local_number, cashier_name, dt)
	head["DOCTYPE"] = doctype
	if subtype is not None:
		head["DOCSUBTYPE"] = subtype
	if order_ret_num:
		head["ORDERRETNUM"] = order_ret_num
	if order_ret_cash_register:
		head["ORDERRETCASHREGNUM"] = order_ret_cash_register
	if order_ret_date:
		head["ORDERRETDATE"] = order_ret_date
	if order_storno_num:
		head["ORDERSTORNUM"] = order_storno_num
	if revoke_last_online_document:
		head["REVOKELASTONLINEDOC"] = "true"
	if testing:
		head["TESTING"] = "true"
	if offline:
		head["OFFLINE"] = "true"
		head["ORDERTAXNUM"] = order_tax_num
		if prev_doc_hash:
			head["PREVDOCHASH"] = prev_doc_hash
	return head


def _wrap_check(head_xml: str, body: str = "") -> bytes:
	xml = (
		'<?xml version="1.0" encoding="windows-1251"?>'
		'<CHECK xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
		'xsi:noNamespaceSchemaLocation="check01.xsd">'
		f"<CHECKHEAD>{head_xml}</CHECKHEAD>{body}</CHECK>"
	)
	return xml.encode("windows-1251")


def build_service_document(head: dict) -> bytes:
	"""Технічні документи: відкриття/закриття зміни, початок/кінець офлайн сесії."""
	return _wrap_check(_ordered(head, _CHECKHEAD_ORDER))


def build_service_cash_document(head: dict, total: float) -> bytes:
	"""Службове внесення/видача: CHECKHEAD + CHECKTOTAL без товарних рядків."""
	return _wrap_check(
		_ordered(head, _CHECKHEAD_ORDER),
		f"<CHECKTOTAL><SUM>{_fmt_sum(total)}</SUM></CHECKTOTAL>",
	)


def build_sale_check(
	head: dict,
	items: list[dict],
	payments: list[dict],
	total: float,
	taxes: list[dict] | None = None,
	no_rounding_total: float | None = None,
	rounding_sum: float | None = None,
) -> bytes:
	"""Чек реалізації/повернення (тип за DOCSUBTYPE у head).

	items:    [{code, name, uom, qty, price, amount, uktzed?, unit_cd?, letters?,
	            discount_type?, subtotal?, discount_percent?, discount_sum?, excise_labels?,
	            tobacco_weight?, tobacco_qty?, alcohol_strength?, alcohol_volume?}]
	payments: [{code, name, sum, provided?, remains?}]  (code: 0-готівка, 1-картка)
	taxes:    [{type, name, letter, prc, sign, turnover, sum}]  (лише для платників ПДВ)
	"""
	base_total = frappe.utils.flt(no_rounding_total if no_rounding_total is not None else total)
	excluded_tax = sum(
		frappe.utils.flt(tax.get("sum")) for tax in (taxes or []) if tax.get("sign")
	)
	line_total = sum(frappe.utils.flt(item.get("amount")) for item in items)
	if abs(line_total + excluded_tax - base_total) > 0.02:
		raise ValueError("Сума товарних рядків не дорівнює CHECKTOTAL")
	if abs(sum(frappe.utils.flt(payment.get("sum")) for payment in payments) - frappe.utils.flt(total)) > 0.02:
		raise ValueError("Сума форм оплати не дорівнює CHECKTOTAL")
	if taxes and any(not item.get("letters") for item in items):
		raise ValueError("Для оподатковуваного чека кожен товар повинен мати PRRO Tax Letters")

	def paysys_xml(rows: list[dict]) -> str:
		order = [
			"tax_num", "name", "acquire_id", "acquire_pn", "acquire_name", "transaction_id",
			"transaction_date", "transaction_number", "device_id", "epz_details", "auth_code",
			"client_info", "sum", "commission",
		]
		tags = {
			"tax_num": "TAXNUM", "name": "NAME", "acquire_id": "ACQUIREID", "acquire_pn": "ACQUIREPN",
			"acquire_name": "ACQUIRENM", "transaction_id": "ACQUIRETRANSID",
			"transaction_date": "POSTRANSDATE", "transaction_number": "POSTRANSNUM",
			"device_id": "DEVICEID", "epz_details": "EPZDETAILS", "auth_code": "AUTHCD",
			"client_info": "CLIENTINFO", "sum": "SUM", "commission": "COMMISSION",
		}
		result = []
		for rownum, values in enumerate(rows, start=1):
			content = []
			for key in order:
				value = values.get(key)
				if value is None or value == "":
					continue
				value = _fmt_sum(value) if key in {"sum", "commission"} else escape(str(value))
				content.append(f"<{tags[key]}>{value}</{tags[key]}>")
			result.append(f'<ROW ROWNUM="{rownum}">{"".join(content)}</ROW>')
		return f"<PAYSYS>{''.join(result)}</PAYSYS>" if result else ""

	pay_rows = []
	for i, pay in enumerate(payments, start=1):
		pay_rows.append(
			f'<ROW ROWNUM="{i}">'
			f"<PAYFORMCD>{pay['code']}</PAYFORMCD>"
			f"<PAYFORMNM>{escape(pay['name'])}</PAYFORMNM>"
			f"<SUM>{_fmt_sum(pay['sum'])}</SUM>"
			+ (f"<PROVIDED>{_fmt_sum(pay['provided'])}</PROVIDED>" if pay.get("provided") is not None else "")
			+ (f"<REMAINS>{_fmt_sum(pay['remains'])}</REMAINS>" if pay.get("remains") is not None else "")
			+ paysys_xml(pay.get("paysys") or [])
			+ "</ROW>"
		)

	tax_xml = ""
	if taxes:
		tax_rows = []
		for i, tax in enumerate(taxes, start=1):
			tax_rows.append(
				f'<ROW ROWNUM="{i}">'
				f"<TYPE>{tax.get('type', 0)}</TYPE>"
				f"<NAME>{escape(tax['name'])}</NAME>"
				+ (f"<LETTER>{escape(tax['letter'])}</LETTER>" if tax.get("letter") else "")
				+ f"<PRC>{frappe.utils.flt(tax['prc']):.2f}</PRC>"
				+ (f"<SIGN>{'true' if tax.get('sign') else 'false'}</SIGN>")
				+ f"<TURNOVER>{_fmt_sum(tax['turnover'])}</TURNOVER>"
				+ f"<SUM>{_fmt_sum(tax['sum'])}</SUM>"
				+ "</ROW>"
			)
		tax_xml = "<CHECKTAX>" + "".join(tax_rows) + "</CHECKTAX>"

	body_rows = []
	for i, item in enumerate(items, start=1):
		row = [f'<ROW ROWNUM="{i}">', f"<CODE>{escape(str(item.get('code') or i))}</CODE>"]
		if item.get("barcode"):
			row.append(f"<BARCODE>{escape(str(item['barcode']))}</BARCODE>")
		if item.get("uktzed"):
			row.append(f"<UKTZED>{escape(str(item['uktzed']))}</UKTZED>")
		elif item.get("dkpp"):
			row.append(f"<DKPP>{escape(str(item['dkpp']))}</DKPP>")
		row.append(f"<NAME>{escape(item['name'])}</NAME>")
		if item.get("description"):
			row.append(f"<DESCRIPTION>{escape(item['description'])}</DESCRIPTION>")
		if item.get("unit_cd"):
			row.append(f"<UNITCD>{escape(str(item['unit_cd']))}</UNITCD>")
		row.append(f"<UNITNM>{escape(item.get('uom') or 'шт')}</UNITNM>")
		row.append(f"<AMOUNT>{frappe.utils.flt(item['qty']):g}</AMOUNT>")
		if item.get("tobacco_weight") is not None:
			row.append(f"<TOBACCOWEIGHT>{frappe.utils.flt(item['tobacco_weight']):g}</TOBACCOWEIGHT>")
		if item.get("tobacco_qty") is not None:
			row.append(f"<TOBACCOQT>{int(item['tobacco_qty'])}</TOBACCOQT>")
		if item.get("alcohol_strength") is not None:
			row.append(f"<ALCOSTRENGTH>{frappe.utils.flt(item['alcohol_strength']):g}</ALCOSTRENGTH>")
		if item.get("alcohol_volume") is not None:
			row.append(f"<ALCOVOL>{frappe.utils.flt(item['alcohol_volume']):g}</ALCOVOL>")
		row.append(f"<PRICE>{_fmt_sum(item['price'])}</PRICE>")
		if item.get("letters"):
			row.append(f"<LETTERS>{escape(item['letters'])}</LETTERS>")
		row.append(f"<COST>{_fmt_sum(item['amount'])}</COST>")
		if item.get("discount_sum") is not None and frappe.utils.flt(item.get("discount_sum")):
			discount_type = int(item.get("discount_type", 0))
			if discount_type not in (0, 1):
				raise ValueError("Тип знижки/надбавки має бути 0 або 1")
			subtotal = item.get("subtotal")
			if subtotal is None:
				subtotal = frappe.utils.flt(item["amount"]) + (
					frappe.utils.flt(item["discount_sum"])
					if discount_type == 0
					else -frappe.utils.flt(item["discount_sum"])
				)
			row.append(f"<DISCOUNTTYPE>{discount_type}</DISCOUNTTYPE>")
			row.append(f"<SUBTOTAL>{_fmt_sum(subtotal)}</SUBTOTAL>")
			if item.get("discount_percent") is not None:
				row.append(f"<DISCOUNTPERCENT>{_fmt_sum(item['discount_percent'])}</DISCOUNTPERCENT>")
			row.append(f"<DISCOUNTSUM>{_fmt_sum(item['discount_sum'])}</DISCOUNTSUM>")
		excise_labels = [str(value).strip() for value in (item.get("excise_labels") or []) if str(value).strip()]
		if excise_labels:
			labels_xml = "".join(
				f'<ROW ROWNUM="{index}"><EXCISELABEL>{escape(value)}</EXCISELABEL></ROW>'
				for index, value in enumerate(excise_labels, start=1)
			)
			row.append(f"<EXCISELABELS>{labels_xml}</EXCISELABELS>")
		row.append("</ROW>")
		body_rows.append("".join(row))

	total_xml = f"<SUM>{_fmt_sum(total)}</SUM>"
	if no_rounding_total is not None and abs(base_total - frappe.utils.flt(total)) > 0.001:
		rounding_sum = base_total - frappe.utils.flt(total) if rounding_sum is None else rounding_sum
		total_xml += (
			f"<RNDSUM>{_fmt_sum(rounding_sum)}</RNDSUM>"
			f"<NORNDSUM>{_fmt_sum(base_total)}</NORNDSUM>"
		)
	body = (
		f"<CHECKTOTAL>{total_xml}</CHECKTOTAL>"
		+ "<CHECKPAY>" + "".join(pay_rows) + "</CHECKPAY>"
		+ tax_xml
		+ "<CHECKBODY>" + "".join(body_rows) + "</CHECKBODY>"
	)
	return _wrap_check(_ordered(head, _CHECKHEAD_ORDER), body)


def build_zrep(
	*,
	fop: dict,
	register: dict,
	local_number: int,
	cashier_name: str,
	realiz: dict,
	returns: dict | None = None,
	service_input: float = 0,
	service_output: float = 0,
	posting_datetime=None,
	testing: bool = False,
	offline: bool = False,
	prev_doc_hash: str | None = None,
	order_tax_num: str | None = None,
) -> bytes:
	"""Z-звіт (zrep01).

	realiz / returns: {sum, count, payforms: [{code, name, sum}], taxes: [...]}
	"""
	dt = posting_datetime or frappe.utils.now_datetime()
	head = _common_head(fop, register, local_number, cashier_name, dt)
	if testing:
		head["TESTING"] = "true"
	if offline:
		head["OFFLINE"] = "true"
		head["ORDERTAXNUM"] = order_tax_num
		if prev_doc_hash:
			head["PREVDOCHASH"] = prev_doc_hash

	def _section(tag: str, data: dict) -> str:
		payforms = "".join(
			f'<ROW ROWNUM="{i}"><PAYFORMCD>{p["code"]}</PAYFORMCD>'
			f"<PAYFORMNM>{escape(p['name'])}</PAYFORMNM><SUM>{_fmt_sum(p['sum'])}</SUM></ROW>"
			for i, p in enumerate(data.get("payforms", []), start=1)
		)
		taxes = "".join(
			f'<ROW ROWNUM="{i}"><TYPE>{t.get("type", 0)}</TYPE><NAME>{escape(t["name"])}</NAME>'
			+ (f"<LETTER>{escape(t['letter'])}</LETTER>" if t.get("letter") else "")
			+ f"<PRC>{frappe.utils.flt(t['prc']):.2f}</PRC><SIGN>{'true' if t.get('sign') else 'false'}</SIGN>"
			+ f"<TURNOVER>{_fmt_sum(t['turnover'])}</TURNOVER><SUM>{_fmt_sum(t['sum'])}</SUM></ROW>"
			for i, t in enumerate(data.get("taxes", []), start=1)
		)
		inner = f"<SUM>{_fmt_sum(data.get('sum', 0))}</SUM><ORDERSCNT>{data.get('count', 0)}</ORDERSCNT>"
		if payforms:
			inner += f"<PAYFORMS>{payforms}</PAYFORMS>"
		if taxes:
			inner += f"<TAXES>{taxes}</TAXES>"
		return f"<{tag}>{inner}</{tag}>"

	body = _section("ZREPREALIZ", realiz)
	if returns and returns.get("count"):
		body += _section("ZREPRETURN", returns)
	zbody = ""
	if service_input or service_output:
		zbody = "<ZREPBODY>"
		if service_input:
			zbody += f"<SERVICEINPUT>{_fmt_sum(service_input)}</SERVICEINPUT>"
		if service_output:
			zbody += f"<SERVICEOUTPUT>{_fmt_sum(service_output)}</SERVICEOUTPUT>"
		zbody += "</ZREPBODY>"

	xml = (
		'<?xml version="1.0" encoding="windows-1251"?>'
		'<ZREP xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
		'xsi:noNamespaceSchemaLocation="zrep01.xsd">'
		f"<ZREPHEAD>{_ordered(head, _ZREPHEAD_ORDER)}</ZREPHEAD>{body}{zbody}</ZREP>"
	)
	return xml.encode("windows-1251")
