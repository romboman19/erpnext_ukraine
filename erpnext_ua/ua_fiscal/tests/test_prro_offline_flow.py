"""Наскрізний offline/recovery тест ПРРО без зовнішніх мережевих викликів.

Запуск:

    bench --site <site> execute erpnext_ua.ua_fiscal.tests.test_prro_offline_flow.run
"""

import frappe

from erpnext_ua.ua_fiscal import orchestration as orch
from erpnext_ua.ua_fiscal import xml_builder as xb
from erpnext_ua.ua_fiscal.fiscal_client import FiscalTransportError
from erpnext_ua.ua_fiscal.tests.test_prro_flow import TESTNAME, _cleanup, _test_company


class OfflineFailoverClient:
	def __init__(self):
		self.settings = frappe.get_single("PRRO Settings")
		self.settings.mode = "Тестовий"
		self.settings.offline_queue_enabled = 1
		self.document_calls = 0
		self.package_calls = 0
		self.signed_online = 0
		self.signed_offline = 0

	def sign(self, data: bytes, kep_key: str, *, online: bool = True) -> bytes:
		if data.lstrip().startswith(b"<?xml"):
			xb.validate_document(data)
		if online:
			self.signed_online += 1
		else:
			self.signed_offline += 1
		return b"SIGNED:" + data

	def send_document(self, signed: bytes) -> bytes:
		self.document_calls += 1
		if self.document_calls == 2:
			raise FiscalTransportError("імітація timeout після передачі", uncertain=True)
		return self._ticket(6000000000 + self.document_calls)

	def send_package(self, signed: bytes) -> bytes:
		self.package_calls += 1
		return self._ticket(6999999999)

	@staticmethod
	def _ticket(fiscal_number: int) -> bytes:
		return (
			'<?xml version="1.0" encoding="windows-1251"?>'
			"<TICKET><UID>x</UID>"
			f"<ORDERTAXNUM>{fiscal_number}</ORDERTAXNUM>"
			"<OFFLINESESSIONID>82564</OFFLINESESSIONID>"
			"<OFFLINESEED>179625192271940</OFFLINESEED>"
			"<ERRORCODE>0</ERRORCODE><VER>1</VER></TICKET>"
		).encode("windows-1251")


def _prepare_fixture():
	frappe.set_user("Administrator")
	company = _test_company()
	_cleanup(company)
	fop = frappe.get_doc(
		{
			"doctype": "FOP Profile",
			"company": company,
			"fop_full_name": "Тест Тестович",
			"prro_registered_name": "ТЕСТ ТЕСТОВИЧ",
			"tax_id": "3184710691",
			"single_tax_group": "2",
			"tax_rate_mode": "Фіксована ставка",
		}
	).insert(ignore_permissions=True)
	key_file = frappe.get_doc(
		{"doctype": "File", "file_name": "dummy-offline.key", "is_private": 1, "content": "dummy"}
	).insert(ignore_permissions=True)
	kep = frappe.get_doc(
		{
			"doctype": "UA KEP Key",
			"user": "Administrator",
			"subject_name": "Касир Тестовий",
			"tax_id": "3184710691",
			"status": "Active",
			"key_file": key_file.file_url,
			"key_password": "x",
		}
	).insert(ignore_permissions=True)
	register = frappe.get_doc(
		{
			"doctype": "PRRO Cash Register",
			"register_name": TESTNAME,
			"fop_profile": fop.name,
			"fiscal_number": "4000099999",
			"register_local_number": 10,
			"unit_name": "Інтернет-магазин HUNTER",
			"unit_address": "м. Рівне, вул. Тестова, 1",
			"default_kep_key": kep.name,
			"device_registered": 1,
			"offline_session_id": "82563",
			"offline_seed": "179625192271939",
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
	return company, register, kep


def run():
	company, register, kep = _prepare_fixture()
	client = OfflineFailoverClient()
	shift_name = orch.open_shift(register.name, kep.name, client=client)

	receipt_name = orch.fiscalize_sale(
		register.name,
		kep.name,
		items=[{"code": "SKU1", "name": "Ніж", "uom": "шт", "qty": 1, "price": 450, "amount": 450}],
		payments=[{"code": 0, "name": "ГОТІВКА", "sum": 450}],
		total=450,
		idem_key="offline-selftest-sale",
		client=client,
	)
	receipt = frappe.get_doc("PRRO Receipt", receipt_name)
	# Після відкриття зміни сервер видав оновлений резерв 82564; саме його має
	# використати автоматичний failover, а не застаріле значення з фікстури.
	assert receipt.status == "Offline" and receipt.fiscal_number.startswith("82564."), receipt.as_dict()
	register.reload()
	assert register.runtime_state == "Offline" and register.active_offline_session
	assert register.get_password("offline_seed") == "179625192271940"

	orch.close_shift(register.name, kep.name, client=client)
	session = orch.end_offline_session(register.active_offline_session, client=client)
	state_calls = []

	def registrar_state(fiscal_number, kep_key, **extra):
		state_calls.append(extra)
		return {
			"ShiftState": 0,
			"NextLocalNum": 10,
			"OfflineSessionId": "82564",
			"OfflineSeed": "179625192271940",
			"OfflineSessionsMonthlyDuration": 0,
			"Closed": False,
		}

	client.registrar_state = registrar_state
	orch.flush_offline_session(session.name, client=client)

	session.reload()
	register.reload()
	shift = frappe.get_doc("PRRO Shift", shift_name)
	assert session.status == "Delivered"
	assert register.runtime_state == "Online" and not register.active_offline_session
	assert shift.status == "Closed"
	assert client.package_calls == 1
	assert client.signed_offline == 5, client.signed_offline
	assert state_calls == [{
		"OfflineSessionId": session.session_id,
		"OfflineSeed": "179625192271940",
	}], state_calls
	assert not frappe.db.exists(
		"PRRO Receipt", {"cash_register": register.name, "status": ("in", ("Offline", "Uncertain"))}
	)

	result = {
		"shift": shift.name,
		"session": session.name,
		"offline_receipt": receipt.fiscal_number,
		"offline_documents": client.signed_offline,
		"packages": client.package_calls,
	}
	_cleanup(company)
	return result
