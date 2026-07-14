"""Production-оркестрація ПРРО: online, offline, ідемпотентність і recovery."""

from __future__ import annotations

import base64
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from functools import wraps

import frappe

from erpnext_ua.ua_fiscal import offline_fiscal as offline
from erpnext_ua.ua_fiscal import xml_builder as xb
from erpnext_ua.ua_fiscal.fiscal_client import (
	FiscalClient,
	FiscalProtocolError,
	FiscalServerError,
	FiscalTransportError,
)

PAYFORM_CASH = 0
PAYFORM_CARD = 1
OFFLINE_SESSION_LIMIT_MINUTES = 36 * 60
OFFLINE_MONTH_LIMIT_MINUTES = 168 * 60
OFFLINE_PACKAGE_TARGET_BYTES = 175 * 1024


def _serialize_register_operation(function):
	"""Не допускає паралельних /doc для однієї каси навіть між web workers."""
	@wraps(function)
	def wrapped(*args, **kwargs):
		register_name = kwargs.get("cash_register") or (args[0] if args else None)
		if not register_name:
			return function(*args, **kwargs)
		# Redis lock переживає проміжні DB commit, потрібні для durable ledger.
		# 10 хвилин покривають два документи закриття навіть за DPS timeout=120s.
		with frappe.cache.lock(
			f"erpnext_ua:prro:{register_name}", timeout=600, blocking_timeout=30
		):
			return function(*args, **kwargs)

	return wrapped


def _response_for_storage(response: bytes) -> str:
	if not response:
		return ""
	if response.lstrip().startswith(b"<"):
		return response.decode("windows-1251", errors="replace")
	return "base64:" + base64.b64encode(response).decode()


def parse_ticket(response: bytes, client: FiscalClient | None = None) -> dict:
	"""Перевіряє CMS-відповідь (якщо є) та розбирає ticket01."""
	content = response
	if not content.lstrip().startswith(b"<"):
		if not client:
			raise FiscalProtocolError("Підписану квитанцію неможливо перевірити без FiscalClient")
		content = client.unwrap(content)
	m = re.search(rb"<TICKET[\s>].*?</TICKET>", content, re.S)
	if not m:
		raise FiscalProtocolError(f"Квитанцію не знайдено у відповіді: {content[:200]!r}")
	try:
		root = ET.fromstring(m.group(0).decode("windows-1251"))
	except (UnicodeDecodeError, ET.ParseError) as exc:
		raise FiscalProtocolError("Квитанція ДПС містить некоректний XML") from exc

	def get(tag):
		element = root.find(tag)
		return element.text if element is not None else None

	try:
		code = int(get("ERRORCODE") or 0)
	except ValueError as exc:
		raise FiscalProtocolError("Квитанція ДПС містить некоректний ERRORCODE") from exc
	if code:
		raise FiscalServerError(
			f"Фіскальний сервер ERRORCODE={code}: {get('ERRORTEXT') or 'невідома помилка'}",
			error_code=code,
		)
	return {
		"uid": get("UID"),
		"error_code": code,
		"order_tax_num": get("ORDERTAXNUM"),
		"offline_session_id": get("OFFLINESESSIONID"),
		"offline_seed": get("OFFLINESEED"),
		"order_num": get("ORDERNUM"),
	}


def _fop_dict(fop_profile: str) -> dict:
	return frappe.db.get_value(
		"FOP Profile",
		fop_profile,
		["fop_full_name", "prro_registered_name", "tax_id", "vat_payer", "vat_number"],
		as_dict=True,
	)


def _register_dict(register) -> dict:
	return {
		"unit_name": register.unit_name,
		"unit_address": register.unit_address,
		"fiscal_number": register.fiscal_number,
		"local_number": int(register.register_local_number or 1),
	}


def _testing_flag(client) -> bool:
	return client.settings.mode == "Тестовий"


def _cashier(kep_key: str) -> tuple[str, str]:
	values = frappe.db.get_value("UA KEP Key", kep_key, ["subject_name", "user"], as_dict=True)
	if not values:
		frappe.throw(f"Ключ КЕП {kep_key} не знайдено", FiscalServerError)
	return values.subject_name or values.user, values.user


def _build_qr(register_fn: str, fiscal_num: str, total, dt) -> str:
	return (
		f"https://cabinet.tax.gov.ua/cashregs/check?id={fiscal_num}"
		f"&fn={register_fn}&sm={frappe.utils.flt(total):.2f}"
		f"&date={dt.strftime('%Y%m%d')}&time={dt.strftime('%H%M')}"
	)


def _kind_label(kind: str) -> str:
	return {
		"Sale": "Продаж",
		"Return": "Повернення",
		"Service In": "Службове внесення",
		"Service Out": "Службова видача",
		"Open Shift": "Відкриття зміни",
		"Close Shift": "Закриття зміни",
		"Z Report": "Z-звіт",
		"Offline Begin": "Початок офлайн",
		"Offline End": "Завершення офлайн",
	}.get(kind, kind)


def _idem_key(prefix: str, register: str, reference: str | None) -> str:
	return f"{prefix}:{register}:{reference or frappe.generate_hash(length=24)}"


def _existing_receipt(idem_key: str | None):
	if not idem_key:
		return None
	name = frappe.db.get_value("PRRO Receipt", {"idem_key": idem_key}, "name")
	return frappe.get_doc("PRRO Receipt", name) if name else None


def _new_ledger(
	*,
	register,
	shift,
	kind: str,
	local_number: int,
	xml: bytes,
	idem_key: str,
	total: float = 0,
	sales_invoice: str | None = None,
	pos_order: str | None = None,
	related_receipt: str | None = None,
	payment_summary: list[dict] | None = None,
	tax_summary: list[dict] | None = None,
	offline_session: str | None = None,
	fiscal_number: str | None = None,
	previous_hash: str | None = None,
):
	head = ET.fromstring(xml).find("CHECKHEAD") if xml.lstrip().startswith(b"<?xml") else None
	if head is None:
		try:
			head = ET.fromstring(xml).find("ZREPHEAD")
		except ET.ParseError:
			head = None
	uid = head.findtext("UID") if head is not None else None
	doc = frappe.get_doc(
		{
			"doctype": "PRRO Receipt",
			"cash_register": register.name,
			"shift": shift.name,
			"receipt_type": _kind_label(kind),
			"receipt_kind": kind,
			"status": "Draft",
			"sales_invoice": sales_invoice,
			"pos_order": pos_order,
			"related_receipt": related_receipt,
			"local_number": local_number,
			"fiscal_number": fiscal_number,
			"uid": uid,
			"idem_key": idem_key,
			"total_amount": total,
			"receipt_xml": xml.decode("windows-1251"),
			"is_offline": 1 if offline_session else 0,
			"offline_session": offline_session,
			"previous_document_hash": previous_hash,
			"payment_summary": frappe.as_json(payment_summary or []),
			"tax_summary": frappe.as_json(tax_summary or []),
		}
	).insert(ignore_permissions=True)
	return doc


def _persist_signed(receipt, signed: bytes, status: str) -> str:
	digest = offline.doc_hash(signed)
	frappe.db.set_value(
		"PRRO Receipt",
		receipt.name,
		{
			"status": status,
			"signature_status": "CAdES-E-T" if status == "Sending" else "CAdES-BES (offline)",
			"signed_document_base64": base64.b64encode(signed).decode(),
			"signed_document_hash": digest,
			"response_state": "Not Sent",
		},
		update_modified=False,
	)
	receipt.reload()
	frappe.db.commit()
	return digest


def _send_online(client: FiscalClient, receipt, xml: bytes, kep_key: str) -> dict:
	xb.validate_document(xml)
	signed = client.sign(xml, kep_key, online=True)
	_persist_signed(receipt, signed, "Sending")
	try:
		response = client.send_document(signed)
	except FiscalTransportError as exc:
		frappe.db.set_value(
			"PRRO Receipt",
			receipt.name,
			{
				"status": "Uncertain" if exc.uncertain else "Error",
				"response_state": "Uncertain" if exc.uncertain else "Not Sent",
				"error_message": str(exc)[:500],
				"retry_count": int(receipt.retry_count or 0) + 1,
			},
			update_modified=False,
		)
		frappe.db.commit()
		raise
	except FiscalServerError as exc:
		# HTTP 4xx / локальна protocol-validation означають певну відмову:
		# документ не можна позначати як невизначено доставлений.
		frappe.db.set_value(
			"PRRO Receipt",
			receipt.name,
			{
				"status": "Error",
				"response_state": "Rejected" if exc.error_code is not None else "Not Sent",
				"error_message": str(exc)[:500],
				"retry_count": int(receipt.retry_count or 0) + 1,
			},
			update_modified=False,
		)
		frappe.db.commit()
		raise
	try:
		ticket = parse_ticket(response, client)
	except (FiscalProtocolError, FiscalTransportError) as exc:
		frappe.db.set_value(
			"PRRO Receipt",
			receipt.name,
			{
				"status": "Uncertain",
				"response_state": "Uncertain",
				"dps_response": _response_for_storage(response),
				"error_message": str(exc)[:500],
			},
			update_modified=False,
		)
		frappe.db.commit()
		raise
	except FiscalServerError as exc:
		# Криптографічно перевірена квитанція з ERRORCODE є остаточною
		# відповіддю ДПС, а не transport uncertainty.
		frappe.db.set_value(
			"PRRO Receipt",
			receipt.name,
			{
				"status": "Error",
				"response_state": "Rejected",
				"dps_response": _response_for_storage(response),
				"error_message": str(exc)[:500],
			},
			update_modified=False,
		)
		frappe.db.commit()
		raise
	frappe.db.set_value(
		"PRRO Receipt",
		receipt.name,
		{
			"status": "Fiscalized",
			"response_state": "Confirmed",
			"fiscal_number": ticket["order_tax_num"],
			"fiscalized_at": frappe.utils.now_datetime(),
			"dps_response": _response_for_storage(response),
			"error_message": None,
		},
		update_modified=False,
	)
	frappe.db.commit()
	return ticket


def _assert_shift_key(shift, kep_key: str):
	if shift.kep_key and shift.kep_key != kep_key:
		frappe.throw(
			f"Усі документи зміни {shift.name} мають підписуватися тим самим КЕП ({shift.kep_key})",
			FiscalServerError,
		)


def _assert_no_unresolved(register_name: str):
	name = frappe.db.get_value(
		"PRRO Receipt",
		{"cash_register": register_name, "status": ("in", ("Draft", "Signed", "Sending", "Uncertain", "Error"))},
		"name",
		order_by="local_number asc",
	)
	if name:
		frappe.throw(
			f"Перед наступним документом потрібно відновити стан фіскального документа {name}",
			FiscalServerError,
		)


def _set_offline_seed(doctype: str, name: str, seed: str | None):
	"""Оновлює Password-поле через штатне шифроване сховище Frappe."""
	if seed in (None, ""):
		return
	from frappe.utils.password import set_encrypted_password

	seed = str(seed)
	set_encrypted_password(doctype, name, seed, "offline_seed")
	frappe.db.set_value(doctype, name, "offline_seed", "*" * len(seed), update_modified=False)


def _update_offline_reserve(register, ticket: dict):
	values = {}
	if ticket.get("offline_session_id"):
		values["offline_session_id"] = ticket["offline_session_id"]
	if values:
		frappe.db.set_value("PRRO Cash Register", register.name, values, update_modified=False)
	if ticket.get("offline_seed"):
		_set_offline_seed("PRRO Cash Register", register.name, ticket["offline_seed"])


def _state_value(state: dict, key: str):
	"""Читає поля відповіді ДПС толерантно до різного casing у старих стендах."""
	if key in state:
		return state[key]
	wanted = key.casefold()
	for candidate, value in state.items():
		if str(candidate).casefold() == wanted:
			return value
	return None


def _block_register(register_name: str, message: str):
	frappe.db.set_value(
		"PRRO Cash Register", register_name, "runtime_state", "Blocked", update_modified=False
	)
	frappe.db.commit()
	raise FiscalProtocolError(message)


def _apply_register_state(register, state: dict, *, enforce_local_number: bool = True):
	if not state:
		_block_register(register.name, "ДПС не повернула стан зареєстрованого ПРРО")

	values = {"last_server_sync": frappe.utils.now_datetime()}
	monthly = _state_value(state, "OfflineSessionsMonthlyDuration")
	if monthly is not None:
		values["offline_month_minutes"] = int(monthly or 0)
	offline_id = _state_value(state, "OfflineSessionId")
	offline_seed = _state_value(state, "OfflineSeed")
	if offline_id:
		values["offline_session_id"] = str(offline_id)
	if _state_value(state, "Closed"):
		values.update({"status": "Disabled", "runtime_state": "Blocked"})
	elif not register.active_offline_session and register.runtime_state in {"Blocked", "Error", "Recovering"}:
		# Успішна ручна звірка номера через sync_register_state є штатним
		# способом зняти локальне блокування після усунення його причини.
		values["runtime_state"] = "Online"
	frappe.db.set_value("PRRO Cash Register", register.name, values, update_modified=False)
	if offline_seed:
		_set_offline_seed("PRRO Cash Register", register.name, str(offline_seed))
	if _state_value(state, "Closed"):
		_block_register(register.name, "Реєстрацію ПРРО скасовано на фіскальному сервері ДПС")

	server_next = _state_value(state, "NextLocalNum")
	if enforce_local_number and server_next is not None:
		server_next = int(server_next)
		local_next = int(register.next_local_number or 1)
		if server_next <= 0:
			_block_register(register.name, f"ДПС повернула некоректний NextLocalNum={server_next}")
		if server_next != local_next:
			has_history = bool(frappe.db.exists("PRRO Receipt", {"cash_register": register.name}))
			if not has_history and not register.current_shift and not register.active_offline_session:
				# Чисте встановлення може безпечно підхопити вже існуючий номер із ДПС.
				frappe.db.set_value(
					"PRRO Cash Register", register.name, "next_local_number", server_next, update_modified=False
				)
				register.next_local_number = server_next
			else:
				_block_register(
					register.name,
					f"Розбіжність наскрізної нумерації ПРРО: локально очікується {local_next}, "
					f"ДПС очікує {server_next}. Відправку заблоковано до звірки журналу.",
				)
	frappe.db.commit()
	return state


@frappe.whitelist()
def register_device(cash_register: str, kep_key: str | None = None, forced: bool = False, client=None) -> dict:
	client = client or FiscalClient()
	register = frappe.get_doc("PRRO Cash Register", cash_register)
	kep_key = kep_key or register.default_kep_key
	if not kep_key:
		frappe.throw("Для реєстрації пристрою потрібен КЕП", FiscalServerError)
	result = client.device_register(
		register.fiscal_number, register.device_id, kep_key, forced=bool(frappe.utils.cint(forced))
	)
	server_device = result.get("DeviceId") or result.get("deviceId")
	if server_device and str(server_device) != register.device_id:
		_block_register(
			register.name,
			"Цей фіскальний номер ПРРО зареєстровано на іншому пристрої. "
			"Автоматичну фіскалізацію заблоковано; примусовий DeviceRegister дозволений лише оператору.",
		)
	if not server_device:
		_block_register(register.name, "ДПС не повернула DeviceId після команди DeviceRegister")
	frappe.db.set_value(
		"PRRO Cash Register",
		register.name,
		{"device_registered": 1, "device_registered_at": frappe.utils.now_datetime()},
		update_modified=False,
	)
	frappe.db.commit()
	return result


@frappe.whitelist()
def sync_register_state(cash_register: str, kep_key: str | None = None, client=None) -> dict:
	client = client or FiscalClient()
	register = frappe.get_doc("PRRO Cash Register", cash_register)
	kep_key = kep_key or register.default_kep_key
	if not kep_key:
		frappe.throw("Для запиту стану ПРРО потрібен КЕП", FiscalServerError)
	state = client.registrar_state(register.fiscal_number, kep_key) or {}
	return _apply_register_state(register, state)


def _preflight_online_document(register, kep_key: str, client, *, expected_shift_state: int):
	"""Звіряє пристрій, серверну зміну і наступний номер перед кожним /doc."""
	if hasattr(client, "device_register"):
		register_device(register.name, kep_key, client=client)
		register.reload()
	if not hasattr(client, "registrar_state"):
		return {}  # лише test doubles; production FiscalClient завжди має команду стану
	state = client.registrar_state(register.fiscal_number, kep_key) or {}
	_apply_register_state(register, state)
	shift_state = _state_value(state, "ShiftState")
	if shift_state is None or int(shift_state) != int(expected_shift_state):
		_block_register(
			register.name,
			f"Стан зміни не збігається: ERPNext очікує {expected_shift_state}, "
			f"ДПС повернула {shift_state if shift_state is not None else 'немає даних'}.",
		)
	server_testing = _state_value(state, "Testing")
	if expected_shift_state == 1 and server_testing is not None:
		server_testing = (
			server_testing
			if isinstance(server_testing, bool)
			else str(server_testing).strip().casefold() in {"1", "true", "yes"}
		)
		if server_testing != bool(_testing_flag(client)):
			_block_register(
				register.name,
				"Поточна зміна ДПС має інший режим TESTING; змішувати тестові й фіскальні документи заборонено.",
			)
	return state


def _create_shift(register, kep_key: str, operational_shift: str | None = None):
	_cashier_name, user = _cashier(kep_key)
	return frappe.get_doc(
		{
			"doctype": "PRRO Shift",
			"cash_register": register.name,
			"cashier": user,
			"kep_key": kep_key,
			"operational_shift": operational_shift,
			"fop_profile": register.fop_profile,
			"status": "Opening",
		}
	).insert(ignore_permissions=True)


@frappe.whitelist()
@_serialize_register_operation
def open_shift(
	cash_register: str,
	kep_key: str,
	client: FiscalClient | None = None,
	operational_shift: str | None = None,
) -> str:
	client = client or FiscalClient()
	frappe.db.sql("select name from `tabPRRO Cash Register` where name=%s for update", cash_register)
	register = frappe.get_doc("PRRO Cash Register", cash_register)
	if register.current_shift:
		return register.current_shift
	if register.status != "Active" or register.runtime_state == "Blocked":
		frappe.throw(f"Каса ПРРО {cash_register} недоступна: {register.status}/{register.runtime_state}")
	if not register.active_offline_session:
		_assert_no_unresolved(register.name)

	cashier_name, _user = _cashier(kep_key)
	if register.active_offline_session:
		shift = _create_shift(register, kep_key, operational_shift)
		receipt = _queue_offline_service(
			register=register,
			shift=shift,
			kep_key=kep_key,
			kind="Open Shift",
			doctype=xb.DOCTYPE_OPEN_SHIFT,
			client=client,
		)
		shift.db_set("status", "Open", update_modified=False)
		shift.db_set("opened_at", frappe.utils.now_datetime(), update_modified=False)
		shift.db_set("opening_fiscal_number", receipt.fiscal_number, update_modified=False)
		shift.db_set("opening_local_number", receipt.local_number, update_modified=False)
		frappe.db.set_value("PRRO Cash Register", register.name, "current_shift", shift.name, update_modified=False)
		frappe.db.commit()
		return shift.name

	# Preflight відбувається до створення локальної зміни, тому помилка звірки
	# не лишає orphan-зміну зі статусом Opening/Error.
	_preflight_online_document(register, kep_key, client, expected_shift_state=0)
	register.reload()
	shift = _create_shift(register, kep_key, operational_shift)
	local_number = register.allocate_local_number()
	head = xb.build_check_head(
		doctype=xb.DOCTYPE_OPEN_SHIFT,
		fop=_fop_dict(register.fop_profile),
		register=_register_dict(register),
		local_number=local_number,
		cashier_name=cashier_name,
		testing=_testing_flag(client),
	)
	xml = xb.build_service_document(head)
	receipt = _new_ledger(
		register=register,
		shift=shift,
		kind="Open Shift",
		local_number=local_number,
		xml=xml,
		idem_key=_idem_key("open-shift", register.name, shift.name),
	)
	try:
		_send_online(client, receipt, xml, kep_key)
	except Exception:
		shift.db_set("status", "Error", update_modified=False)
		frappe.db.commit()
		raise
	receipt.reload()
	_finalize_confirmed_receipt(receipt, register, shift, client)
	frappe.db.commit()
	return shift.name


def _offline_seed(session) -> str:
	seed = session.get_password("offline_seed", raise_exception=False)
	if not seed:
		raise FiscalProtocolError(f"В офлайн-сесії {session.name} відсутнє секретне число")
	return seed


def _offline_number(session, register, dt, global_local: int, total=None, previous_hash=None) -> str:
	frappe.db.sql("select name from `tabPRRO Offline Session` where name=%s for update", session.name)
	session_local = int(
		frappe.db.get_value("PRRO Offline Session", session.name, "next_session_local_number", for_update=True) or 1
	)
	check = offline.control_number(
		_offline_seed(session),
		dt.strftime("%d%m%Y"),
		dt.strftime("%H%M%S"),
		global_local,
		register.fiscal_number,
		register.register_local_number,
		f"{frappe.utils.flt(total):.2f}" if total is not None else None,
		previous_hash,
	)
	frappe.db.set_value(
		"PRRO Offline Session",
		session.name,
		"next_session_local_number",
		session_local + 1,
		update_modified=False,
	)
	return offline.offline_fiscal_number(session.session_id, session_local, check)


def _offline_limits(session):
	now = frappe.utils.now_datetime()
	duration = max(0, int((now - frappe.utils.get_datetime(session.started_at)).total_seconds() // 60))
	monthly = int(session.monthly_minutes_at_start or 0) + duration
	if duration >= OFFLINE_SESSION_LIMIT_MINUTES or monthly >= OFFLINE_MONTH_LIMIT_MINUTES:
		frappe.db.set_value("PRRO Offline Session", session.name, "status", "Blocked", update_modified=False)
		frappe.db.set_value("PRRO Cash Register", session.cash_register, "runtime_state", "Blocked", update_modified=False)
		frappe.db.commit()
		frappe.throw(
			"Досягнуто законодавчий ліміт роботи ПРРО офлайн (36 годин за сесію або 168 годин за місяць)",
			FiscalServerError,
		)


def _queue_signed_offline(receipt, xml: bytes, kep_key: str, client, session, *, financial: bool):
	xb.validate_document(xml)
	signed = client.sign(xml, kep_key, online=False)
	digest = _persist_signed(receipt, signed, "Offline")
	values = {"documents_count": int(session.documents_count or 0) + 1}
	if financial:
		values["last_document_hash"] = digest
	frappe.db.set_value("PRRO Offline Session", session.name, values, update_modified=False)
	frappe.db.commit()
	receipt.reload()
	session.reload()
	return receipt


def start_offline_session(
	register,
	shift,
	kep_key: str,
	client,
	*,
	revoke_last_online_document: bool = False,
):
	if register.active_offline_session:
		return frappe.get_doc("PRRO Offline Session", register.active_offline_session)
	session_id = register.offline_session_id
	seed = register.get_password("offline_seed", raise_exception=False)
	if not session_id or not seed:
		frappe.throw(
			"ДПС не надала резервний ID/секрет офлайн-сесії. Безпечний перехід в офлайн неможливий.",
			FiscalServerError,
		)
	session = frappe.get_doc(
		{
			"doctype": "PRRO Offline Session",
			"cash_register": register.name,
			"shift": shift.name,
			"kep_key": kep_key,
			"status": "Opening",
			"session_id": session_id,
			"offline_seed": seed,
			"started_at": frappe.utils.now_datetime(),
			"monthly_minutes_at_start": int(register.offline_month_minutes or 0),
			"next_session_local_number": 1,
			"revoke_last_online_document": 1 if revoke_last_online_document else 0,
		}
	).insert(ignore_permissions=True)
	dt = frappe.utils.now_datetime()
	global_local = register.allocate_local_number()
	fiscal_number = _offline_number(session, register, dt, global_local)
	cashier_name, _user = _cashier(kep_key)
	head = xb.build_check_head(
		doctype=xb.DOCTYPE_OFFLINE_BEGIN,
		fop=_fop_dict(register.fop_profile),
		register=_register_dict(register),
		local_number=global_local,
		cashier_name=cashier_name,
		posting_datetime=dt,
		offline=True,
		order_tax_num=fiscal_number,
		revoke_last_online_document=revoke_last_online_document,
	)
	xml = xb.build_service_document(head)
	receipt = _new_ledger(
		register=register,
		shift=shift,
		kind="Offline Begin",
		local_number=global_local,
		xml=xml,
		idem_key=f"offline-begin:{register.name}:{session_id}",
		offline_session=session.name,
		fiscal_number=fiscal_number,
	)
	_queue_signed_offline(receipt, xml, kep_key, client, session, financial=False)
	session.db_set("status", "Open", update_modified=False)
	frappe.db.set_value(
		"PRRO Cash Register",
		register.name,
		{"active_offline_session": session.name, "runtime_state": "Offline"},
		update_modified=False,
	)
	frappe.db.commit()
	return session


def _queue_offline_service(*, register, shift, kep_key, kind, doctype, client, total=None):
	session = frappe.get_doc("PRRO Offline Session", register.active_offline_session)
	_offline_limits(session)
	_assert_shift_key(shift, kep_key)
	dt = frappe.utils.now_datetime()
	global_local = register.allocate_local_number()
	financial = total is not None
	previous_hash = session.last_document_hash if financial else None
	fiscal_number = _offline_number(session, register, dt, global_local, total, previous_hash)
	cashier_name, _user = _cashier(kep_key)
	head = xb.build_check_head(
		doctype=xb.DOCTYPE_SALE if financial else doctype,
		subtype=(xb.SUBTYPE_SERVICE_DEPOSIT if kind == "Service In" else xb.SUBTYPE_SERVICE_ISSUE) if financial else None,
		fop=_fop_dict(register.fop_profile),
		register=_register_dict(register),
		local_number=global_local,
		cashier_name=cashier_name,
		posting_datetime=dt,
		offline=True,
		prev_doc_hash=previous_hash,
		order_tax_num=fiscal_number,
	)
	xml = xb.build_service_cash_document(head, total) if financial else xb.build_service_document(head)
	receipt = _new_ledger(
		register=register,
		shift=shift,
		kind=kind,
		local_number=global_local,
		xml=xml,
		idem_key=_idem_key(kind.lower().replace(" ", "-"), register.name, f"{session.name}:{global_local}"),
		total=total or 0,
		offline_session=session.name,
		fiscal_number=fiscal_number,
		previous_hash=previous_hash,
	)
	return _queue_signed_offline(receipt, xml, kep_key, client, session, financial=financial)


def _queue_offline_sale(
	*,
	register,
	shift,
	kep_key,
	items,
	payments,
	total,
	taxes,
	no_rounding_total,
	rounding_sum,
	receipt_kind,
	sales_invoice,
	pos_order,
	related_receipt,
	idem_key,
	client,
):
	session = frappe.get_doc("PRRO Offline Session", register.active_offline_session)
	_offline_limits(session)
	_assert_shift_key(shift, kep_key)
	dt = frappe.utils.now_datetime()
	global_local = register.allocate_local_number()
	previous_hash = session.last_document_hash or None
	fiscal_number = _offline_number(session, register, dt, global_local, total, previous_hash)
	cashier_name, _user = _cashier(kep_key)
	related_fiscal = None
	related_register = None
	related_date = None
	if related_receipt:
		related = frappe.get_doc("PRRO Receipt", related_receipt)
		related_fiscal = related.fiscal_number
		related_register = frappe.db.get_value("PRRO Cash Register", related.cash_register, "fiscal_number")
		related_date = frappe.utils.getdate(related.fiscalized_at or related.creation).strftime("%d%m%Y")
	head = xb.build_check_head(
		doctype=xb.DOCTYPE_SALE,
		subtype=xb.SUBTYPE_RETURN if receipt_kind == "Return" else xb.SUBTYPE_GOODS,
		fop=_fop_dict(register.fop_profile),
		register=_register_dict(register),
		local_number=global_local,
		cashier_name=cashier_name,
		posting_datetime=dt,
		testing=_testing_flag(client),
		offline=True,
		prev_doc_hash=previous_hash,
		order_tax_num=fiscal_number,
		order_ret_num=related_fiscal,
		order_ret_cash_register=related_register,
		order_ret_date=related_date,
	)
	xml = xb.build_sale_check(
		head,
		items=items,
		payments=payments,
		total=total,
		taxes=taxes,
		no_rounding_total=no_rounding_total,
		rounding_sum=rounding_sum,
	)
	receipt = _new_ledger(
		register=register,
		shift=shift,
		kind=receipt_kind,
		local_number=global_local,
		xml=xml,
		idem_key=idem_key,
		total=total,
		sales_invoice=sales_invoice,
		pos_order=pos_order,
		related_receipt=related_receipt,
		payment_summary=payments,
		tax_summary=taxes,
		offline_session=session.name,
		fiscal_number=fiscal_number,
		previous_hash=previous_hash,
	)
	receipt = _queue_signed_offline(receipt, xml, kep_key, client, session, financial=True)
	receipt.db_set(
		"qr_data",
		_build_qr(register.fiscal_number, fiscal_number, total, dt),
		update_modified=False,
	)
	receipt.reload()
	return receipt


@frappe.whitelist()
@_serialize_register_operation
def fiscalize_sale(
	cash_register: str,
	kep_key: str,
	items: list[dict],
	payments: list[dict],
	total: float,
	taxes: list[dict] | None = None,
	no_rounding_total: float | None = None,
	rounding_sum: float | None = None,
	receipt_type: str = "Продаж",
	sales_invoice: str | None = None,
	related_receipt: str | None = None,
	client: FiscalClient | None = None,
	pos_order: str | None = None,
	idem_key: str | None = None,
) -> str:
	client = client or FiscalClient()
	receipt_kind = "Return" if receipt_type == "Повернення" else "Sale"
	idem_key = idem_key or _idem_key(
		receipt_kind.lower(), cash_register, sales_invoice or pos_order
	)
	existing = _existing_receipt(idem_key)
	if existing:
		return existing.name
	frappe.db.sql("select name from `tabPRRO Cash Register` where name=%s for update", cash_register)
	register = frappe.get_doc("PRRO Cash Register", cash_register)
	if not register.current_shift:
		frappe.throw(f"На касі {cash_register} немає відкритої зміни", FiscalServerError)
	shift = frappe.get_doc("PRRO Shift", register.current_shift)
	if shift.status not in {"Open", "Closing"}:
		frappe.throw(f"Зміна {shift.name} має статус {shift.status}", FiscalServerError)
	_assert_shift_key(shift, kep_key)
	if receipt_kind == "Return":
		if not related_receipt:
			frappe.throw("Для повернення потрібен первинний фіскальний чек", FiscalServerError)
		related = frappe.get_doc("PRRO Receipt", related_receipt)
		if related.receipt_kind != "Sale" or related.status not in {"Fiscalized", "Offline"}:
			frappe.throw("Первинний документ не є чинним чеком продажу", FiscalServerError)
	if register.active_offline_session:
		return _queue_offline_sale(
			register=register,
			shift=shift,
			kep_key=kep_key,
			items=items,
			payments=payments,
			total=total,
			taxes=taxes or [],
			no_rounding_total=no_rounding_total,
			rounding_sum=rounding_sum,
			receipt_kind=receipt_kind,
			sales_invoice=sales_invoice,
			pos_order=pos_order,
			related_receipt=related_receipt,
			idem_key=idem_key,
			client=client,
		).name

	_assert_no_unresolved(register.name)
	_preflight_online_document(register, kep_key, client, expected_shift_state=1)
	register.reload()
	dt = frappe.utils.now_datetime()
	local_number = register.allocate_local_number()
	cashier_name, _user = _cashier(kep_key)
	related_fiscal = related_register = related_date = None
	if related_receipt:
		related = frappe.get_doc("PRRO Receipt", related_receipt)
		related_fiscal = related.fiscal_number
		related_register = frappe.db.get_value("PRRO Cash Register", related.cash_register, "fiscal_number")
		related_date = frappe.utils.getdate(related.fiscalized_at or related.creation).strftime("%d%m%Y")
	head = xb.build_check_head(
		doctype=xb.DOCTYPE_SALE,
		subtype=xb.SUBTYPE_RETURN if receipt_kind == "Return" else xb.SUBTYPE_GOODS,
		fop=_fop_dict(register.fop_profile),
		register=_register_dict(register),
		local_number=local_number,
		cashier_name=cashier_name,
		posting_datetime=dt,
		testing=_testing_flag(client),
		order_ret_num=related_fiscal,
		order_ret_cash_register=related_register,
		order_ret_date=related_date,
	)
	xml = xb.build_sale_check(
		head,
		items=items,
		payments=payments,
		total=total,
		taxes=taxes,
		no_rounding_total=no_rounding_total,
		rounding_sum=rounding_sum,
	)
	receipt = _new_ledger(
		register=register,
		shift=shift,
		kind=receipt_kind,
		local_number=local_number,
		xml=xml,
		idem_key=idem_key,
		total=total,
		sales_invoice=sales_invoice,
		pos_order=pos_order,
		related_receipt=related_receipt,
		payment_summary=payments,
		tax_summary=taxes,
	)
	try:
		ticket = _send_online(client, receipt, xml, kep_key)
	except FiscalTransportError as exc:
		if not client.settings.offline_queue_enabled or not register.offline_session_id:
			raise
		# Якщо запит гарантовано не був доставлений, повертаємо виданий номер у
		# allocator до створення OFFLINE_BEGIN. За невизначеної доставки номер
		# повторно використовувати не можна: OFFLINE_BEGIN відкликає останній
		# потенційно прийнятий online-документ згідно з протоколом ДПС.
		if not exc.uncertain:
			current_next = int(
				frappe.db.get_value("PRRO Cash Register", register.name, "next_local_number", for_update=True)
				or 1
			)
			if current_next != local_number + 1:
				raise FiscalProtocolError(
					"Локальний номер уже використано наступним документом; автоматичний перехід офлайн зупинено"
				) from exc
			frappe.db.set_value(
				"PRRO Cash Register", register.name, "next_local_number", local_number, update_modified=False
			)
			frappe.db.set_value(
				"PRRO Receipt",
				receipt.name,
				{
					"status": "Cancelled",
					"response_state": "Not Sent",
					"error_message": "Скасовано локально: запит гарантовано не доставлено; операцію перенесено офлайн",
				},
				update_modified=False,
			)
		frappe.db.set_value(
			"PRRO Receipt", receipt.name, "idem_key", f"{idem_key}:uncertain:{local_number}", update_modified=False
		)
		start_offline_session(
			register,
			shift,
			kep_key,
			client,
			revoke_last_online_document=bool(exc.uncertain),
		)
		register.reload()
		return _queue_offline_sale(
			register=register,
			shift=shift,
			kep_key=kep_key,
			items=items,
			payments=payments,
				total=total,
				taxes=taxes or [],
				no_rounding_total=no_rounding_total,
				rounding_sum=rounding_sum,
			receipt_kind=receipt_kind,
			sales_invoice=sales_invoice,
			pos_order=pos_order,
			related_receipt=related_receipt,
			idem_key=idem_key,
			client=client,
		).name
	receipt.reload()
	frappe.db.set_value(
		"PRRO Receipt",
		receipt.name,
		"qr_data",
		_build_qr(register.fiscal_number, ticket["order_tax_num"], total, dt),
		update_modified=False,
	)
	frappe.db.commit()
	return receipt.name


@frappe.whitelist()
@_serialize_register_operation
def fiscalize_service_cash(
	cash_register: str,
	kep_key: str,
	amount: float,
	direction: str,
	idem_key: str,
	client=None,
) -> str:
	client = client or FiscalClient()
	existing = _existing_receipt(idem_key)
	if existing:
		return existing.name
	frappe.db.sql("select name from `tabPRRO Cash Register` where name=%s for update", cash_register)
	register = frappe.get_doc("PRRO Cash Register", cash_register)
	if not register.current_shift:
		frappe.throw("Службова операція потребує відкритої зміни ПРРО")
	shift = frappe.get_doc("PRRO Shift", register.current_shift)
	_assert_shift_key(shift, kep_key)
	kind = "Service In" if direction == "In" else "Service Out"
	if register.active_offline_session:
		receipt = _queue_offline_service(
			register=register,
			shift=shift,
			kep_key=kep_key,
			kind=kind,
			doctype=xb.DOCTYPE_SALE,
			client=client,
			total=amount,
		)
		receipt.db_set("idem_key", idem_key, update_modified=False)
		return receipt.name
	_assert_no_unresolved(register.name)
	_preflight_online_document(register, kep_key, client, expected_shift_state=1)
	register.reload()
	dt = frappe.utils.now_datetime()
	local_number = register.allocate_local_number()
	cashier_name, _user = _cashier(kep_key)
	head = xb.build_check_head(
		doctype=xb.DOCTYPE_SALE,
		subtype=xb.SUBTYPE_SERVICE_DEPOSIT if direction == "In" else xb.SUBTYPE_SERVICE_ISSUE,
		fop=_fop_dict(register.fop_profile),
		register=_register_dict(register),
		local_number=local_number,
		cashier_name=cashier_name,
		posting_datetime=dt,
		testing=_testing_flag(client),
	)
	xml = xb.build_service_cash_document(head, amount)
	receipt = _new_ledger(
		register=register,
		shift=shift,
		kind=kind,
		local_number=local_number,
		xml=xml,
		idem_key=idem_key,
		total=amount,
	)
	_send_online(client, receipt, xml, kep_key)
	return receipt.name


def _shift_totals(shift_name: str) -> dict:
	rows = frappe.get_all(
		"PRRO Receipt",
		filters={"shift": shift_name, "status": ("in", ("Fiscalized", "Offline"))},
		fields=["receipt_kind", "total_amount", "payment_summary", "tax_summary"],
	)
	buckets = {
		"realiz": {"sum": 0.0, "count": 0, "payforms": [], "taxes": []},
		"returns": {"sum": 0.0, "count": 0, "payforms": [], "taxes": []},
	}
	payforms = {"realiz": defaultdict(float), "returns": defaultdict(float)}
	taxes = {"realiz": defaultdict(lambda: defaultdict(float)), "returns": defaultdict(lambda: defaultdict(float))}
	service_input = service_output = 0.0
	for row in rows:
		if row.receipt_kind == "Service In":
			service_input += frappe.utils.flt(row.total_amount)
			continue
		if row.receipt_kind == "Service Out":
			service_output += frappe.utils.flt(row.total_amount)
			continue
		if row.receipt_kind not in {"Sale", "Return"}:
			continue
		key = "returns" if row.receipt_kind == "Return" else "realiz"
		buckets[key]["sum"] += frappe.utils.flt(row.total_amount)
		buckets[key]["count"] += 1
		for payment in json.loads(row.payment_summary or "[]"):
			payforms[key][(int(payment["code"]), payment["name"])] += frappe.utils.flt(payment["sum"])
		for tax in json.loads(row.tax_summary or "[]"):
			tax_key = (int(tax.get("type", 0)), tax["name"], tax.get("letter"), frappe.utils.flt(tax["prc"]))
			taxes[key][tax_key]["turnover"] += frappe.utils.flt(tax["turnover"])
			taxes[key][tax_key]["sum"] += frappe.utils.flt(tax["sum"])
	for key in ("realiz", "returns"):
		buckets[key]["sum"] = frappe.utils.flt(buckets[key]["sum"], 2)
		buckets[key]["payforms"] = [
			{"code": code, "name": name, "sum": frappe.utils.flt(amount, 2)}
			for (code, name), amount in sorted(payforms[key].items())
		]
		buckets[key]["taxes"] = [
			{
				"type": tax_key[0],
				"name": tax_key[1],
				"letter": tax_key[2],
				"prc": tax_key[3],
				"turnover": frappe.utils.flt(values["turnover"], 2),
				"sum": frappe.utils.flt(values["sum"], 2),
			}
			for tax_key, values in taxes[key].items()
		]
	return {**buckets, "service_input": service_input, "service_output": service_output}


def _queue_offline_zrep(register, shift, kep_key, totals, client):
	session = frappe.get_doc("PRRO Offline Session", register.active_offline_session)
	_offline_limits(session)
	dt = frappe.utils.now_datetime()
	global_local = register.allocate_local_number()
	previous_hash = session.last_document_hash or None
	fiscal_number = _offline_number(session, register, dt, global_local, None, previous_hash)
	cashier_name, _user = _cashier(kep_key)
	xml = xb.build_zrep(
		fop=_fop_dict(register.fop_profile),
		register=_register_dict(register),
		local_number=global_local,
		cashier_name=cashier_name,
		realiz=totals["realiz"],
		returns=totals["returns"],
		service_input=totals["service_input"],
		service_output=totals["service_output"],
		posting_datetime=dt,
		testing=_testing_flag(client),
		offline=True,
		prev_doc_hash=previous_hash,
		order_tax_num=fiscal_number,
	)
	receipt = _new_ledger(
		register=register,
		shift=shift,
		kind="Z Report",
		local_number=global_local,
		xml=xml,
		idem_key=_idem_key("z-report", register.name, shift.name),
		offline_session=session.name,
		fiscal_number=fiscal_number,
		previous_hash=previous_hash,
	)
	return _queue_signed_offline(receipt, xml, kep_key, client, session, financial=True)


@frappe.whitelist()
@_serialize_register_operation
def close_shift(cash_register: str, kep_key: str, client: FiscalClient | None = None) -> str:
	client = client or FiscalClient()
	frappe.db.sql("select name from `tabPRRO Cash Register` where name=%s for update", cash_register)
	register = frappe.get_doc("PRRO Cash Register", cash_register)
	if not register.current_shift:
		frappe.throw(f"На касі {cash_register} немає відкритої зміни", FiscalServerError)
	shift = frappe.get_doc("PRRO Shift", register.current_shift)
	_assert_shift_key(shift, kep_key)
	if shift.status in {"Closed", "Closed Offline"}:
		return shift.name
	totals = _shift_totals(shift.name)

	if register.active_offline_session:
		shift.db_set("status", "Closing", update_modified=False)
		z_receipt = _queue_offline_zrep(register, shift, kep_key, totals, client)
		close_receipt = _queue_offline_service(
			register=register,
			shift=shift,
			kep_key=kep_key,
			kind="Close Shift",
			doctype=xb.DOCTYPE_CLOSE_SHIFT,
			client=client,
		)
		frappe.db.set_value(
			"PRRO Shift",
			shift.name,
			{
				"status": "Closed Offline",
				"closed_at": frappe.utils.now_datetime(),
				"closing_fiscal_number": close_receipt.fiscal_number,
				"closing_local_number": close_receipt.local_number,
				"z_report_fiscal_number": z_receipt.fiscal_number,
				"z_report_xml": z_receipt.receipt_xml,
				"sales_total": totals["realiz"]["sum"],
				"refunds_total": totals["returns"]["sum"],
				"receipts_count": totals["realiz"]["count"] + totals["returns"]["count"],
			},
			update_modified=False,
		)
		frappe.db.set_value("PRRO Cash Register", register.name, "current_shift", None, update_modified=False)
		frappe.db.commit()
		return shift.name

	_assert_no_unresolved(register.name)
	_preflight_online_document(register, kep_key, client, expected_shift_state=1)
	register.reload()
	shift.db_set("status", "Closing", update_modified=False)
	fop = _fop_dict(register.fop_profile)
	cashier_name, _user = _cashier(kep_key)
	z_idem = _idem_key("z-report", register.name, shift.name)
	z_ledger = _existing_receipt(z_idem)
	if z_ledger:
		if z_ledger.status != "Fiscalized":
			raise FiscalProtocolError(
				f"Z-звіт {z_ledger.name} має статус {z_ledger.status}; спочатку потрібна звірка з ДПС"
			)
		zrep = z_ledger.receipt_xml.encode("windows-1251")
	else:
		z_local = register.allocate_local_number()
		zrep = xb.build_zrep(
			fop=fop,
			register=_register_dict(register),
			local_number=z_local,
			cashier_name=cashier_name,
			realiz=totals["realiz"],
			returns=totals["returns"],
			service_input=totals["service_input"],
			service_output=totals["service_output"],
			testing=_testing_flag(client),
		)
		z_ledger = _new_ledger(
			register=register,
			shift=shift,
			kind="Z Report",
			local_number=z_local,
			xml=zrep,
			idem_key=z_idem,
		)
		_send_online(client, z_ledger, zrep, kep_key)
		z_ledger.reload()
	_finalize_confirmed_receipt(z_ledger, register, shift, client)
	frappe.db.commit()

	# Z-звіт також споживає наскрізний номер. Повторна звірка перед документом
	# закриття виконує офіційну вимогу перевіряти номер перед кожним /doc.
	_preflight_online_document(register, kep_key, client, expected_shift_state=1)
	register.reload()
	close_idem = _idem_key("close-shift", register.name, shift.name)
	close_ledger = _existing_receipt(close_idem)
	if close_ledger:
		if close_ledger.status != "Fiscalized":
			raise FiscalProtocolError(
				f"Документ закриття {close_ledger.name} має статус {close_ledger.status}; спочатку потрібна звірка з ДПС"
			)
	else:
		c_local = register.allocate_local_number()
		head = xb.build_check_head(
			doctype=xb.DOCTYPE_CLOSE_SHIFT,
			fop=fop,
			register=_register_dict(register),
			local_number=c_local,
			cashier_name=cashier_name,
			testing=_testing_flag(client),
		)
		close_xml = xb.build_service_document(head)
		close_ledger = _new_ledger(
			register=register,
			shift=shift,
			kind="Close Shift",
			local_number=c_local,
			xml=close_xml,
			idem_key=close_idem,
		)
		_send_online(client, close_ledger, close_xml, kep_key)
		close_ledger.reload()
	_finalize_confirmed_receipt(close_ledger, register, shift, client)
	frappe.db.commit()
	return shift.name


def end_offline_session(session_name: str, client=None):
	client = client or FiscalClient()
	frappe.db.sql("select name from `tabPRRO Offline Session` where name=%s for update", session_name)
	session = frappe.get_doc("PRRO Offline Session", session_name)
	if session.status in {"Queued", "Sending", "Delivered"}:
		return session
	register = frappe.get_doc("PRRO Cash Register", session.cash_register)
	shift = frappe.get_doc("PRRO Shift", session.shift)
	dt = frappe.utils.now_datetime()
	global_local = register.allocate_local_number()
	previous_hash = session.last_document_hash or None
	fiscal_number = _offline_number(session, register, dt, global_local, None, previous_hash)
	cashier_name, _user = _cashier(session.kep_key)
	head = xb.build_check_head(
		doctype=xb.DOCTYPE_OFFLINE_END,
		fop=_fop_dict(register.fop_profile),
		register=_register_dict(register),
		local_number=global_local,
		cashier_name=cashier_name,
		posting_datetime=dt,
		offline=True,
		prev_doc_hash=previous_hash,
		order_tax_num=fiscal_number,
	)
	xml = xb.build_service_document(head)
	receipt = _new_ledger(
		register=register,
		shift=shift,
		kind="Offline End",
		local_number=global_local,
		xml=xml,
		idem_key=f"offline-end:{register.name}:{session.session_id}",
		offline_session=session.name,
		fiscal_number=fiscal_number,
		previous_hash=previous_hash,
	)
	_queue_signed_offline(receipt, xml, session.kep_key, client, session, financial=False)
	duration = max(0, int((dt - frappe.utils.get_datetime(session.started_at)).total_seconds() // 60))
	frappe.db.set_value(
		"PRRO Offline Session",
		session.name,
		{"status": "Queued", "ended_at": dt, "duration_minutes": duration},
		update_modified=False,
	)
	frappe.db.set_value("PRRO Cash Register", register.name, "runtime_state", "Recovering", update_modified=False)
	frappe.db.commit()
	session.reload()
	return session


def _offline_batches(documents: list[bytes]):
	batch = []
	size = 0
	for document in documents:
		entry_size = 4 + len(document)
		if batch and (len(batch) >= 100 or size + entry_size > OFFLINE_PACKAGE_TARGET_BYTES):
			yield batch
			batch, size = [], 0
		batch.append(document)
		size += entry_size
	if batch:
		yield batch


def flush_offline_session(session_name: str, client=None):
	client = client or FiscalClient()
	frappe.db.sql("select name from `tabPRRO Offline Session` where name=%s for update", session_name)
	session = frappe.get_doc("PRRO Offline Session", session_name)
	if session.status == "Delivered":
		return session
	if session.status == "Open":
		session = end_offline_session(session.name, client)
	if session.status not in {"Queued", "Error"}:
		frappe.throw(f"Офлайн-сесію {session.name} не можна передати у статусі {session.status}")
	rows = frappe.get_all(
		"PRRO Receipt",
		filters={"offline_session": session.name, "status": ("in", ("Offline", "Queued"))},
		fields=["name", "signed_document_base64", "local_number"],
		order_by="local_number asc",
	)
	if not rows or any(not row.signed_document_base64 for row in rows):
		raise FiscalProtocolError(f"Офлайн-сесія {session.name} має неповний підписаний ланцюг")
	register = frappe.get_doc("PRRO Cash Register", session.cash_register)
	if hasattr(client, "registrar_state"):
		# Офіційний протокол вимагає ці два поля саме перед поверненням online.
		state = client.registrar_state(
			register.fiscal_number,
			session.kep_key,
			OfflineSessionId=session.session_id,
			OfflineSeed=_offline_seed(session),
		) or {}
		# До приймання /pck сервер закономірно ще має старий NextLocalNum.
		_apply_register_state(register, state, enforce_local_number=False)
	frappe.db.set_value("PRRO Offline Session", session.name, "status", "Sending", update_modified=False)
	frappe.db.commit()
	last_ticket = {}
	try:
		documents = [base64.b64decode(row.signed_document_base64, validate=True) for row in rows]
		for batch in _offline_batches(documents):
			package = offline.build_offline_package(batch)
			signed_package = client.sign(package, session.kep_key, online=True)
			response = client.send_package(signed_package)
			if response:
				last_ticket = parse_ticket(response, client)
	except Exception as exc:
		retries = int(session.retry_count or 0) + 1
		next_retry = frappe.utils.add_to_date(
			frappe.utils.now_datetime(), minutes=min(60, 2 ** min(retries, 5)), as_datetime=True
		)
		frappe.db.set_value(
			"PRRO Offline Session",
			session.name,
			{"status": "Error", "retry_count": retries, "next_retry_at": next_retry, "last_error": str(exc)[:500]},
			update_modified=False,
		)
		frappe.db.commit()
		raise
	register = frappe.get_doc("PRRO Cash Register", session.cash_register)
	frappe.db.set_value(
		"PRRO Receipt",
		{"offline_session": session.name, "status": ("in", ("Offline", "Queued"))},
		{"status": "Fiscalized", "response_state": "Confirmed", "fiscalized_at": frappe.utils.now_datetime()},
		update_modified=False,
	)
	if session.revoke_last_online_document:
		frappe.db.set_value(
			"PRRO Receipt",
			{"cash_register": register.name, "status": "Uncertain"},
			{"status": "Cancelled", "response_state": "Confirmed", "error_message": "Відкликано під час offline recovery"},
			update_modified=False,
		)
	frappe.db.set_value(
		"PRRO Offline Session",
		session.name,
		{
			"status": "Delivered",
			"last_error": None,
			"server_response": frappe.as_json(last_ticket),
		},
		update_modified=False,
	)
	values = {
		"active_offline_session": None,
		"runtime_state": "Online",
		"offline_month_minutes": int(session.monthly_minutes_at_start or 0) + int(session.duration_minutes or 0),
		"last_server_sync": frappe.utils.now_datetime(),
	}
	if last_ticket.get("offline_session_id"):
		values["offline_session_id"] = last_ticket["offline_session_id"]
	frappe.db.set_value("PRRO Cash Register", register.name, values, update_modified=False)
	if last_ticket.get("offline_seed"):
		_set_offline_seed("PRRO Cash Register", register.name, last_ticket["offline_seed"])
	for shift_name in frappe.get_all(
		"PRRO Receipt", filters={"offline_session": session.name}, distinct=True, pluck="shift"
	):
		if frappe.db.get_value("PRRO Shift", shift_name, "status") == "Closed Offline":
			frappe.db.set_value("PRRO Shift", shift_name, "status", "Closed", update_modified=False)
	frappe.db.commit()
	session.reload()
	return session


@frappe.whitelist()
def reconcile_receipt(receipt_name: str, client=None) -> dict:
	receipt = frappe.get_doc("PRRO Receipt", receipt_name)
	with frappe.cache.lock(
		f"erpnext_ua:prro:{receipt.cash_register}", timeout=600, blocking_timeout=30
	):
		return _reconcile_receipt_locked(receipt_name, client)


def _finalize_confirmed_receipt(receipt, register, shift, client=None):
	"""Idempotently applies local state changes after DPS confirmed a document.

	The HTTP request may time out after DPS has accepted it.  Recovery must then
	update not only the immutable receipt ledger, but also the shift/register
	state which normally follows ``_send_online``.
	"""
	expected_type = _kind_label(receipt.receipt_kind)
	if receipt.receipt_type != expected_type:
		frappe.db.set_value(
			"PRRO Receipt", receipt.name, "receipt_type", expected_type, update_modified=False
		)
	now = receipt.fiscalized_at or frappe.utils.now_datetime()
	if receipt.receipt_kind == "Open Shift":
		current_shift = frappe.db.get_value("PRRO Cash Register", register.name, "current_shift")
		if current_shift and current_shift != shift.name:
			frappe.db.set_value(
				"PRRO Cash Register", register.name, "runtime_state", "Blocked", update_modified=False
			)
			raise FiscalProtocolError(
				f"ДПС підтвердила відкриття зміни {shift.name}, але локально активна інша зміна {current_shift}"
			)
		frappe.db.set_value(
			"PRRO Shift",
			shift.name,
			{
				"status": "Open",
				"opened_at": shift.opened_at or now,
				"opening_fiscal_number": receipt.fiscal_number,
				"opening_local_number": receipt.local_number,
			},
			update_modified=False,
		)
		frappe.db.set_value(
			"PRRO Cash Register",
			register.name,
			{
				"current_shift": shift.name,
				"runtime_state": "Online",
				"last_server_sync": frappe.utils.now_datetime(),
			},
			update_modified=False,
		)
		# A stored ticket can also contain the reserve identifiers required for
		# a later protocol-compliant offline transition.
		if receipt.dps_response and client:
			try:
				stored = receipt.dps_response
				response = (
					base64.b64decode(stored.removeprefix("base64:"))
					if stored.startswith("base64:")
					else stored.encode("windows-1251")
				)
				_update_offline_reserve(register, parse_ticket(response, client))
			except Exception:
				# Shift recovery is already authoritative via DocumentInfoByLocalNum;
				# failure to re-read optional reserve data must not undo it.
				frappe.log_error(frappe.get_traceback(), f"PRRO ticket reserve recovery {receipt.name}")
		return

	if receipt.receipt_kind == "Z Report":
		frappe.db.set_value(
			"PRRO Shift",
			shift.name,
			{
				"status": "Closing" if shift.status != "Closed" else "Closed",
				"z_report_fiscal_number": receipt.fiscal_number,
				"z_report_xml": receipt.receipt_xml,
			},
			update_modified=False,
		)
		return

	if receipt.receipt_kind == "Close Shift":
		totals = _shift_totals(shift.name)
		z_fiscal = frappe.db.get_value(
			"PRRO Receipt",
			{"shift": shift.name, "receipt_kind": "Z Report", "status": "Fiscalized"},
			"fiscal_number",
			order_by="local_number desc",
		)
		frappe.db.set_value(
			"PRRO Shift",
			shift.name,
			{
				"status": "Closed",
				"closed_at": shift.closed_at or now,
				"closing_fiscal_number": receipt.fiscal_number,
				"closing_local_number": receipt.local_number,
				"z_report_fiscal_number": z_fiscal or shift.z_report_fiscal_number,
				"sales_total": totals["realiz"]["sum"],
				"refunds_total": totals["returns"]["sum"],
				"receipts_count": totals["realiz"]["count"] + totals["returns"]["count"],
			},
			update_modified=False,
		)
		frappe.db.set_value(
			"PRRO Cash Register",
			register.name,
			{
				"current_shift": None,
				"runtime_state": "Online",
				"last_server_sync": frappe.utils.now_datetime(),
			},
			update_modified=False,
		)


def _reconcile_receipt_locked(receipt_name: str, client=None) -> dict:
	client = client or FiscalClient()
	receipt = frappe.get_doc("PRRO Receipt", receipt_name)
	if receipt.status == "Fiscalized":
		register = frappe.get_doc("PRRO Cash Register", receipt.cash_register)
		shift = frappe.get_doc("PRRO Shift", receipt.shift)
		_finalize_confirmed_receipt(receipt, register, shift, client)
		frappe.db.commit()
		receipt.reload()
		return receipt.as_dict()
	if receipt.status not in {"Uncertain", "Error"}:
		return receipt.as_dict()
	register = frappe.get_doc("PRRO Cash Register", receipt.cash_register)
	shift = frappe.get_doc("PRRO Shift", receipt.shift)
	info = client.document_info_by_local_number(register.fiscal_number, receipt.local_number, shift.kep_key)
	if info and (info.get("NumFiscal") or info.get("numFiscal")):
		fiscal_number = str(info.get("NumFiscal") or info.get("numFiscal"))
		frappe.db.set_value(
			"PRRO Receipt",
			receipt.name,
			{
				"status": "Fiscalized",
				"response_state": "Confirmed",
				"fiscal_number": fiscal_number,
				"fiscalized_at": frappe.utils.now_datetime(),
				"error_message": None,
			},
			update_modified=False,
		)
		frappe.db.commit()
		receipt.reload()
		_finalize_confirmed_receipt(receipt, register, shift, client)
		frappe.db.commit()
		receipt.reload()
		return receipt.as_dict()

	# Остаточна відмова не повинна назавжди зупиняти касу. Але номер можна
	# повернути allocator-у лише коли ДПС явно очікує саме його, локально це
	# остання спроба і жодного наступного ledger-запису не існує.
	if receipt.status == "Error" and receipt.response_state in {"Not Sent", "Rejected"}:
		state = client.registrar_state(register.fiscal_number, shift.kep_key) or {}
		server_next = _state_value(state, "NextLocalNum")
		local_next = int(
			frappe.db.get_value(
				"PRRO Cash Register", register.name, "next_local_number", for_update=True
			) or 1
		)
		has_later = bool(
			frappe.db.exists(
				"PRRO Receipt",
				{
					"cash_register": register.name,
					"local_number": (">", receipt.local_number),
					"status": ("!=", "Cancelled"),
				},
			)
		)
		if (
			server_next is not None
			and int(server_next) == int(receipt.local_number)
			and local_next == int(receipt.local_number) + 1
			and not has_later
		):
			archived_idem = f"cancelled:{receipt.name}:{(receipt.idem_key or '')[-80:]}"[:140]
			frappe.db.set_value(
				"PRRO Receipt",
				receipt.name,
				{
					"status": "Cancelled",
					"idem_key": archived_idem,
					"error_message": (
						f"{receipt.error_message or 'Документ відхилено'}. "
						"ДПС підтвердила, що локальний номер не спожито; дозволено контрольований повтор."
					)[:500],
				},
				update_modified=False,
			)
			values = {"next_local_number": int(receipt.local_number)}
			if not register.active_offline_session:
				values["runtime_state"] = "Online"
			frappe.db.set_value("PRRO Cash Register", register.name, values, update_modified=False)
			frappe.db.commit()
			receipt.reload()
	return receipt.as_dict()
