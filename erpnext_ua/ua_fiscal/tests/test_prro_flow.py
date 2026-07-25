"""Наскрізний тест оркестрації зміни ПРРО з мок-клієнтом.

Мережа не викликається: FakeClient валідує кожен документ проти вбудованих XSD
і повертає квитанцію з фіскальним номером. Запуск:

    bench --site <site> execute erpnext_ua.ua_fiscal.tests.test_prro_flow.run
"""

import frappe

from erpnext_ua.ua_fiscal import orchestration as orch
from erpnext_ua.ua_fiscal import xml_builder as xb
from erpnext_ua.ua_fiscal.fiscal_client import FiscalServerError, FiscalTransportError

TESTNAME = "_prro_selftest"


def _test_company():
	for company in frappe.get_all("Company", pluck="name"):
		if not frappe.db.exists("FOP Profile", {"company": company}):
			return company
	raise AssertionError("Для PRRO self-test потрібна компанія без чинного FOP Profile")


def _cleanup(company):
	# Тест прибирає лише власні фікстури прямим DB delete: production-контролери
	# навмисно забороняють видалення незмінного фіскального журналу.
	frappe.db.delete("PRRO Receipt", {"cash_register": TESTNAME})
	frappe.db.delete("PRRO Offline Session", {"cash_register": TESTNAME})
	frappe.db.delete("PRRO Shift", {"cash_register": TESTNAME})
	frappe.db.delete("PRRO Cash Register", {"name": TESTNAME})
	frappe.db.delete("UA KEP Key", {"subject_name": "Касир Тестовий"})
	frappe.db.delete("FOP Profile", {"company": company, "fop_full_name": "Тест Тестович"})
	frappe.db.commit()


class FakeFiscalClient:
	"""Замість мережі: XSD-валідація документа + видача фіскального номера."""

	def __init__(self):
		self.settings = frappe.get_single("PRRO Settings")
		self.settings.mode = "Тестовий"
		self.counter = 5000000000
		self.sent = []
		self.next_local = 1
		self.shift_state = 0
		self.device_calls = 0
		self.state_calls = 0
		self.reject_next = False
		self.uncertain_next = False
		self.documents = {}

	def device_register(self, fiscal_number, device_id, kep_key, forced=False):
		assert len(device_id) == 64 and not forced
		self.device_calls += 1
		return {"DeviceId": device_id}

	def registrar_state(self, fiscal_number, kep_key, **extra):
		self.state_calls += 1
		return {
			"ShiftState": self.shift_state,
			"NextLocalNum": self.next_local,
			"Testing": True if self.shift_state else False,
			"OfflineSessionId": "82563",
			"OfflineSeed": "179625192271939",
			"OfflineSessionsMonthlyDuration": 0,
			"Closed": False,
		}

	def document_info_by_local_number(self, fiscal_number, local_number, kep_key):
		return self.documents.get(int(local_number))

	def sign(self, xml: bytes, kep_key: str, *, online: bool = True) -> bytes:
		xb.validate_document(xml)  # кине ValueError, якщо XML невалідний
		from lxml import etree

		self.sent.append(etree.fromstring(xml).tag)
		return b"SIGNED:" + xml

	def send_document(self, signed: bytes) -> bytes:
		from lxml import etree

		root = etree.fromstring(signed.removeprefix(b"SIGNED:"))
		if self.reject_next:
			self.reject_next = False
			return (
				'<?xml version="1.0" encoding="windows-1251"?>'
				"<TICKET><UID>x</UID><ERRORCODE>9</ERRORCODE>"
				"<ERRORTEXT>test rejection</ERRORTEXT><VER>1</VER></TICKET>"
			).encode("windows-1251")
		doctype = root.findtext(".//DOCTYPE")
		if doctype == str(xb.DOCTYPE_OPEN_SHIFT):
			self.shift_state = 1
		elif doctype == str(xb.DOCTYPE_CLOSE_SHIFT):
			self.shift_state = 0
		self.next_local += 1
		self.counter += 1
		local_number = int(root.findtext(".//ORDERNUM"))
		self.documents[local_number] = {"NumFiscal": str(self.counter)}
		if self.uncertain_next:
			self.uncertain_next = False
			raise FiscalTransportError("test timeout after DPS accepted document", uncertain=True)
		return (
			'<?xml version="1.0" encoding="windows-1251"?>'
			'<TICKET xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
			f"<UID>x</UID><ORDERTAXNUM>{self.counter}</ORDERTAXNUM>"
			"<OFFLINESESSIONID>82563</OFFLINESESSIONID>"
			"<OFFLINESEED>179625192271939</OFFLINESEED>"
			"<ERRORCODE>0</ERRORCODE><VER>1</VER></TICKET>"
		).encode("windows-1251")


def run():
	frappe.set_user("Administrator")
	company = _test_company()
	_cleanup(company)

	frappe.get_single("PRRO Settings").db_set("mode", "Тестовий")
	fop = frappe.get_doc({
		"doctype": "FOP Profile", "company": company, "fop_full_name": "Тест Тестович",
		"prro_registered_name": "ТЕСТ ТЕСТОВИЧ",
		"tax_id": "3184710691", "single_tax_group": "2", "tax_rate_mode": "Фіксована ставка",
	}).insert(ignore_permissions=True)
	kf = frappe.get_doc({"doctype": "File", "file_name": "dummy.key", "is_private": 1,
						 "content": "dummy"}).insert(ignore_permissions=True)
	kep = frappe.get_doc({
		"doctype": "UA KEP Key", "user": "Administrator", "subject_name": "Касир Тестовий",
		"tax_id": "3184710691", "status": "Active", "key_file": kf.file_url, "key_password": "x",
	}).insert(ignore_permissions=True)
	frappe.get_doc({
		"doctype": "PRRO Cash Register", "register_name": TESTNAME, "fop_profile": fop.name,
		"fiscal_number": "4000099999", "unit_name": "Інтернет-магазин HUNTER",
		"unit_address": "м. Рівне, вул. Тестова, 1", "default_kep_key": kep.name,
		"device_registered": 1,
	}).insert(ignore_permissions=True)
	frappe.db.commit()

	client = FakeFiscalClient()

	# DPS accepted the opening document but the transport timed out. Recovery
	# must restore both the receipt and the shift/register state idempotently.
	client.uncertain_next = True
	try:
		orch.open_shift(TESTNAME, kep.name, client=client)
	except FiscalTransportError:
		pass
	else:
		raise AssertionError("Тестовий transport timeout мав перервати першу відповідь")
	opening = frappe.get_doc(
		"PRRO Receipt", {"cash_register": TESTNAME, "receipt_kind": "Open Shift"}
	)
	assert opening.status == "Uncertain"
	orch.reconcile_receipt(opening.name, client=client)
	opening.reload()
	shift_name = opening.shift
	assert opening.status == "Fiscalized"
	assert opening.receipt_type == "Відкриття зміни"
	assert frappe.db.get_value("PRRO Shift", shift_name, "status") == "Open"
	assert frappe.db.get_value("PRRO Cash Register", TESTNAME, "current_shift") == shift_name

	r1 = orch.fiscalize_sale(
		TESTNAME, kep.name,
		items=[{"code": "SKU1", "name": "Ніж «Ведмідь»", "uom": "шт", "qty": 2,
				"price": 450.0, "amount": 900.0}],
		payments=[{"code": 0, "name": "ГОТІВКА", "sum": 900.0, "provided": 1000.0, "remains": 100.0}],
		total=900.0, client=client)
	assert frappe.db.get_value("PRRO Receipt", r1, "status") == "Fiscalized"

	r2 = orch.fiscalize_sale(
		TESTNAME, kep.name,
		items=[{"code": "SKU1", "name": "Ніж «Ведмідь»", "uom": "шт", "qty": 1,
				"price": 450.0, "amount": 450.0}],
		payments=[{"code": 0, "name": "ГОТІВКА", "sum": 450.0}],
		total=450.0, receipt_type="Повернення", related_receipt=r1, client=client)

	# Перевірка безпечного recovery остаточно відхиленого останнього номера:
	# ДПС не спожила ORDERNUM=4, reconcile повертає allocator і той самий
	# бізнес-idem можна повторити без пропуску або дублювання.
	client.reject_next = True
	try:
		orch.fiscalize_sale(
			TESTNAME,
			kep.name,
			items=[{"code": "SKU2", "name": "Чохол", "uom": "шт", "qty": 1,
					"price": 10.0, "amount": 10.0}],
			payments=[{"code": 0, "name": "ГОТІВКА", "sum": 10.0}],
			total=10.0,
			idem_key="rejected-retry",
			client=client,
		)
	except FiscalServerError:
		pass
	else:
		raise AssertionError("ДПС rejection мав завершити першу спробу помилкою")
	rejected = frappe.get_doc("PRRO Receipt", {"idem_key": "rejected-retry"})
	assert rejected.status == "Error" and rejected.response_state == "Rejected"
	orch.reconcile_receipt(rejected.name, client=client)
	rejected.reload()
	assert rejected.status == "Cancelled"
	assert frappe.db.get_value("PRRO Cash Register", TESTNAME, "next_local_number") == 4
	retried = orch.fiscalize_sale(
		TESTNAME,
		kep.name,
		items=[{"code": "SKU2", "name": "Чохол", "uom": "шт", "qty": 1,
				"price": 10.0, "amount": 10.0}],
		payments=[{"code": 0, "name": "ГОТІВКА", "sum": 10.0}],
		total=10.0,
		idem_key="rejected-retry",
		client=client,
	)
	assert frappe.db.get_value("PRRO Receipt", retried, "local_number") == 4

	# Z-звіт прийнято ДПС, але перевірка відповіді перервалась. Після
	# reconcile повторний close_shift має використати вже підтверджений Z і
	# надіслати лише окремий документ закриття, без дублю Z-звіту.
	client.uncertain_next = True
	try:
		orch.close_shift(TESTNAME, kep.name, client=client)
	except FiscalTransportError:
		pass
	else:
		raise AssertionError("Тестовий timeout Z-звіту мав перервати першу спробу закриття")
	z_receipt = frappe.get_doc("PRRO Receipt", {"shift": shift_name, "receipt_kind": "Z Report"})
	assert z_receipt.status == "Uncertain"
	orch.reconcile_receipt(z_receipt.name, client=client)
	orch.close_shift(TESTNAME, kep.name, client=client)
	shift = frappe.get_doc("PRRO Shift", shift_name)
	assert shift.status == "Closed"
	assert not frappe.db.get_value("PRRO Cash Register", TESTNAME, "current_shift")
	assert shift.sales_total == 910.0 and shift.refunds_total == 450.0

	nums = sorted(frappe.db.get_value("PRRO Receipt", r, "local_number") for r in (r1, r2))
	assert nums == [2, 3], nums  # відкриття=1, чеки=2,3 — наскрізна нумерація
	assert client.sent == ["CHECK", "CHECK", "CHECK", "CHECK", "CHECK", "ZREP", "CHECK"], client.sent
	assert client.device_calls == 8 and client.state_calls == 9

	print(f"OK: shift {shift_name} відкрито→2 чеки→Z-звіт {shift.z_report_fiscal_number}→закрито; "
		  f"local nums {nums}; docs {client.sent}")
	_cleanup(company)
	return "PASS"
