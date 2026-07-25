"""Safe synchronization of FOP Profile with the private Tax Cabinet API."""

from __future__ import annotations

import base64
import json
import re
from datetime import date, datetime

import frappe
import requests
from frappe import _

from erpnext_ua.ua_fiscal.fiscal_client import (
	FiscalClient,
	FiscalProtocolError,
	FiscalServerError,
	FiscalTransportError,
)

CABINET_PAYER_CARD_URL = "https://cabinet.tax.gov.ua/ws/public_api/payer_card"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MANAGER_ROLES = {"System Manager", "Accounts Manager"}


def _token(value) -> str:
	return re.sub(r"[^0-9A-ZА-ЯІЇЄҐ]+", "_", str(value or "").upper()).strip("_")


def _digits(value) -> str:
	return "".join(ch for ch in str(value or "") if ch.isdigit())


def _valid_signer_tax_ids(signer: dict) -> set[str]:
	"""Return only structurally valid RNOKPP/EDRPOU values from signer metadata.

	Some certificate parsers expose a missing DRFO extension as the placeholder
	``0``. It is metadata absence, not a taxpayer identifier, and must not turn a
	valid DPS authorization into a false mismatch.
	"""
	values = {_digits(signer.get(field)) for field in ("ipn", "edrpou")}
	return {value for value in values if re.fullmatch(r"(?:\d{8}|\d{10})", value)}


def _parse_date(value) -> str | None:
	text = str(value or "").strip()
	if not text:
		return None
	text = text.split(" ", 1)[0]
	for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
		try:
			return datetime.strptime(text, fmt).date().isoformat()
		except ValueError:
			continue
	return None


def _block_rows(block: dict) -> list[tuple[dict, dict]]:
	headers = block.get("headers") if isinstance(block.get("headers"), dict) else {}
	rows = block.get("listValues")
	if not isinstance(rows, list):
		values = block.get("values")
		rows = values if isinstance(values, list) else [values]
	return [(row, headers) for row in rows if isinstance(row, dict)]


def _find_value(
	row: dict,
	headers: dict,
	*,
	keys: tuple[str, ...] = (),
	labels: tuple[str, ...] = (),
):
	normalized = {_token(key): value for key, value in row.items()}
	for key in keys:
		value = normalized.get(_token(key))
		if value not in (None, ""):
			return value
	label_tokens = tuple(_token(label) for label in labels)
	for key, value in row.items():
		label = _token(headers.get(key) or key)
		if value not in (None, "") and any(token in label for token in label_tokens):
			return value
	return None


def _block_map(payload) -> dict[int, dict]:
	if isinstance(payload, dict):
		payload = payload.get("response_result") or payload.get("data") or payload.get("groups") or payload
	if isinstance(payload, dict):
		payload = [payload]
	if not isinstance(payload, list):
		raise ValueError("Tax Cabinet returned an unsupported payer card structure")
	result = {}
	for block in payload:
		if not isinstance(block, dict):
			continue
		try:
			group = int(block.get("idGroup") or block.get("id_group"))
		except (TypeError, ValueError):
			continue
		result[group] = block
	return result


def _status_from_registration(block: dict | None) -> str:
	if not block:
		return "Active"
	text = " ".join(
		str(value or "")
		for row, _headers in _block_rows(block)
		for value in row.values()
	).casefold()
	if any(word in text for word in ("припинен", "закрит", "анульован", "ліквідован")):
		return "Closed"
	if any(word in text for word in ("призупинен", "зупинен")):
		return "Suspended"
	return "Active"


def _parse_vat(block: dict | None) -> tuple[bool, str | None]:
	if not block:
		return False, None
	for row, headers in _block_rows(block):
		number = _find_value(
			row,
			headers,
			keys=("KOD_PDV", "KODPDV", "VAT_NUMBER", "IPN"),
			labels=("НОМЕР ПЛАТНИКА ПДВ", "ІПН ПЛАТНИКА ПДВ"),
		)
		cancelled = _find_value(
			row,
			headers,
			keys=("DAT_ANUL", "DATE_CANCEL", "D_CANCEL"),
			labels=("ДАТА АНУЛЮВАН", "ДАТА СКАСУВАН"),
		)
		text = " ".join(str(value or "") for value in row.values()).casefold()
		if number and not cancelled and "анульован" not in text:
			return True, str(number).strip()
	return False, None


def _parse_single_tax(block: dict | None) -> tuple[str | None, str | None]:
	if not block:
		return None, None
	for row, headers in _block_rows(block):
		group_value = _find_value(
			row,
			headers,
			keys=("GROUP", "GROUP_NUM", "GRUP", "GRUPA", "C_GROUP"),
			labels=("ГРУПА",),
		)
		match = re.search(r"(?:^|\D)([123])(?:\D|$)", str(group_value or ""))
		if not match:
			continue
		registered = _find_value(
			row,
			headers,
			keys=("DATA_N", "DATE_REG", "D_REG", "DAT_REESTR", "REG_DATE"),
			labels=("ДАТА ПЕРЕХОДУ", "ДАТА РЕЄСТРАЦ", "ДАТА ВНЕСЕННЯ"),
		)
		return match.group(1), _parse_date(registered)
	return None, None


def _parse_bank_accounts(block: dict | None) -> list[dict]:
	accounts = []
	if not block:
		return accounts
	for row, headers in _block_rows(block):
		iban = _find_value(
			row,
			headers,
			keys=("CLIENT_COUNT", "IBAN", "ACCOUNT_IBAN", "ACCOUNT"),
			labels=("IBAN", "НОМЕР РАХУНК"),
		)
		iban = str(iban or "").replace(" ", "").upper()
		if not re.fullmatch(r"UA\d{27}", iban):
			continue
		bank_name = _find_value(
			row,
			headers,
			keys=("MFO_NAME", "BANK_NAME"),
			labels=("НАЗВА БАНК", "БАНК"),
		)
		currency = _find_value(
			row,
			headers,
			keys=("CODE_CURRENCY_NAME", "CURRENCY"),
			labels=("ВАЛЮТ",),
		)
		closed = _find_value(
			row,
			headers,
			keys=("DATE_CLOSE_COUNT", "DATE_CLOSE", "D_CLOSE"),
			labels=("ДАТА ЗАКРИТ",),
		)
		accounts.append(
			{
				"iban": iban,
				"bank_name": str(bank_name or "").strip(),
				"currency": str(currency or "").strip(),
				"active": not bool(closed),
			}
		)
	accounts.sort(key=lambda item: (not item["active"], "980" not in item["currency"], item["iban"]))
	return accounts


def _parse_kveds(block: dict | None) -> list[dict]:
	items = []
	if not block:
		return items
	for row, headers in _block_rows(block):
		code_value = _find_value(
			row,
			headers,
			keys=("KVED", "KVED_CODE", "CODE_KVED", "C_KVED", "CODE"),
			labels=("КОД КВЕД", "КВЕД"),
		)
		# The private Tax Cabinet API currently prefixes codes with an
		# underscore (for example ``_47.78``). Underscore is a regex word
		# character, so a leading ``\b`` incorrectly rejected every code.
		match = re.search(r"(?<!\d)\d{2}(?:\.\d{1,2}){1,2}(?!\d)", str(code_value or ""))
		if not match:
			continue
		code = match.group(0)
		title = _find_value(
			row,
			headers,
			keys=("KVED_NAME", "NAME_KVED", "TITLE", "NAME"),
			labels=("НАЗВА ВИДУ ДІЯЛЬНОСТ", "НАЗВА КВЕД"),
		)
		main_value = _find_value(
			row,
			headers,
			keys=("IS_MAIN", "MAIN", "OSN"),
			labels=("ОСНОВН",),
		)
		is_main = str(main_value or "").strip().casefold() in {"1", "true", "так", "основний"}
		items.append({"code": code, "title": str(title or code).strip(), "is_main": is_main})

	seen = set()
	unique = []
	for item in items:
		if item["code"] not in seen:
			unique.append(item)
			seen.add(item["code"])
	if unique and not any(item["is_main"] for item in unique):
		unique[0]["is_main"] = True
	return unique


def parse_taxpayer_card(payload, expected_tax_id: str | None = None) -> dict:
	"""Map documented payer-card blocks to the FOP Profile data model."""
	blocks = _block_map(payload)
	identity_rows = _block_rows(blocks.get(1, {}))
	if not identity_rows:
		raise ValueError("Tax Cabinet did not return identification data")
	identity, identity_headers = identity_rows[0]
	tax_id = _digits(
		_find_value(identity, identity_headers, keys=("TIN", "TIN_S"), labels=("ПОДАТКОВИЙ НОМЕР", "РНОКПП"))
	)
	if expected_tax_id and tax_id != _digits(expected_tax_id):
		raise ValueError("Tax Cabinet returned data for another taxpayer")
	full_name = str(
		_find_value(identity, identity_headers, keys=("FULL_NAME", "NAME"), labels=("ПОВНЕ НАЙМЕНУВАН", "ПІБ"))
		or ""
	).strip()

	registration_rows = _block_rows(blocks.get(2, {}))
	registration, registration_headers = registration_rows[0] if registration_rows else ({}, {})
	address = str(
		_find_value(
			registration,
			registration_headers,
			keys=("ADRESS", "ADDRESS", "TAX_ADDRESS"),
			labels=("ПОДАТКОВА АДРЕС", "АДРЕСА", "МІСЦЕ ПРОЖИВАН"),
		)
		or ""
	).strip()

	vat_payer, vat_number = _parse_vat(blocks.get(5))
	single_tax_group, single_tax_date = _parse_single_tax(blocks.get(6))
	accounts = _parse_bank_accounts(blocks.get(14))
	kveds = _parse_kveds(blocks.get(16))
	updates = {
		"fop_full_name": full_name,
		"prro_registered_name": full_name,
		"tax_id": tax_id,
		"status": _status_from_registration(blocks.get(2)),
		"registration_address": address,
		"vat_payer": 1 if vat_payer else 0,
		"vat_number": vat_number or "",
	}
	if single_tax_group:
		updates["single_tax_group"] = single_tax_group
		updates["tax_rate_mode"] = (
			"Фіксована ставка"
			if single_tax_group in {"1", "2"}
			else ("3% з ПДВ" if vat_payer else "5% без ПДВ")
		)
	if single_tax_date:
		updates["single_tax_registration_date"] = single_tax_date
	if accounts:
		updates["iban"] = accounts[0]["iban"]
		updates["bank_name"] = accounts[0]["bank_name"]
	if kveds:
		updates["kved_main"] = next(item["code"] for item in kveds if item["is_main"])
	return {"updates": updates, "bank_accounts": accounts, "kveds": kveds}


def _require_manager() -> None:
	if frappe.session.user == "Administrator":
		return
	if not MANAGER_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("Only System Manager or Accounts Manager can synchronize FOP data"), frappe.PermissionError)


def _active_key(kep_key: str, tax_id: str):
	key = frappe.get_doc("UA KEP Key", kep_key)
	key.check_permission("read")
	if key.status != "Active":
		frappe.throw(_("The selected KEP key is not active"))
	if key.valid_until and frappe.utils.getdate(key.valid_until) < date.today():
		frappe.throw(_("The selected KEP key has expired"))
	key_tax_id = _digits(key.subject_tax_id)
	if key_tax_id and key_tax_id != tax_id:
		frappe.throw(_("The selected KEP belongs to another taxpayer"))
	return key


def _authorization_signature(tax_id: str, kep_key: str) -> tuple[str, FiscalClient]:
	client = FiscalClient()
	key = _active_key(kep_key, tax_id)
	file_doc = frappe.get_doc("File", {"file_url": key.key_file})
	content = file_doc.get_content()
	if isinstance(content, str):
		content = content.encode()
	body = client._signer_post(
		"/api/sign",
		{
			"key": base64.b64encode(content).decode(),
			"password": key.get_password("key_password"),
			"data": base64.b64encode(tax_id.encode()).decode(),
			"detached": False,
			"tsp": False,
		},
	)
	signer = body.get("signer") if isinstance(body.get("signer"), dict) else {}
	signer_tax_ids = _valid_signer_tax_ids(signer)
	if signer_tax_ids and tax_id not in signer_tax_ids:
		frappe.throw(_("The KEP signer does not match the FOP taxpayer number"))
	try:
		signature = base64.b64decode(body["signature"], validate=True)
	except (KeyError, ValueError) as exc:
		raise FiscalProtocolError("Signer did not return a valid authorization signature") from exc
	return base64.b64encode(signature).decode(), client


def _bounded_response(response) -> bytes:
	try:
		content_length = int(response.headers.get("Content-Length") or 0)
	except (TypeError, ValueError):
		content_length = 0
	if content_length > MAX_RESPONSE_BYTES:
		raise FiscalProtocolError("Tax Cabinet response is too large")
	parts = []
	size = 0
	for chunk in response.iter_content(chunk_size=64 * 1024):
		if not chunk:
			continue
		size += len(chunk)
		if size > MAX_RESPONSE_BYTES:
			raise FiscalProtocolError("Tax Cabinet response is too large")
		parts.append(chunk)
	return b"".join(parts)


def fetch_taxpayer_card(tax_id: str, kep_key: str) -> dict:
	tax_id = _digits(tax_id)
	if not re.fullmatch(r"\d{10}", tax_id):
		frappe.throw(_("FOP taxpayer number must contain 10 digits"))
	authorization, client = _authorization_signature(tax_id, kep_key)
	try:
		response = client.http.get(
			CABINET_PAYER_CARD_URL,
			headers={
				"Authorization": authorization,
				"Accept": "application/json",
				"Content-Type": "application/json",
			},
			timeout=(10, min(max(client.timeout, 10), 30)),
			allow_redirects=False,
			stream=True,
		)
	except requests.RequestException as exc:
		raise FiscalTransportError(f"Tax Cabinet is unavailable: {exc}") from exc
	try:
		if 300 <= response.status_code < 400:
			raise FiscalProtocolError("Tax Cabinet redirect was refused to protect the KEP authorization")
		if response.status_code in {401, 403}:
			raise FiscalServerError("Tax Cabinet rejected the KEP authorization")
		if response.status_code >= 500:
			raise FiscalTransportError(f"Tax Cabinet is temporarily unavailable: HTTP {response.status_code}")
		if response.status_code != 200:
			raise FiscalServerError(f"Tax Cabinet returned HTTP {response.status_code}")
		raw = _bounded_response(response)
	finally:
		response.close()
	try:
		payload = json.loads(raw.decode("utf-8-sig"))
	except (UnicodeDecodeError, json.JSONDecodeError) as exc:
		raise FiscalProtocolError("Tax Cabinet returned invalid JSON") from exc
	try:
		return parse_taxpayer_card(payload, expected_tax_id=tax_id)
	except ValueError as exc:
		raise FiscalProtocolError(str(exc)) from exc


def _selected_account(result: dict, selected_iban: str | None) -> dict | None:
	accounts = result.get("bank_accounts") or []
	if not accounts:
		return None
	iban = str(selected_iban or result["updates"].get("iban") or "").replace(" ", "").upper()
	account = next((item for item in accounts if item["iban"] == iban), None)
	if not account:
		frappe.throw(_("Select an IBAN returned by the Tax Cabinet"))
	result["updates"]["iban"] = account["iban"]
	result["updates"]["bank_name"] = account["bank_name"]
	return account


def _ensure_kveds(result: dict) -> None:
	for item in result.get("kveds") or []:
		if frappe.db.exists("UA KVED", item["code"]):
			continue
		frappe.get_doc(
			{"doctype": "UA KVED", "code": item["code"], "title": item["title"] or item["code"]}
		).insert()


@frappe.whitelist()
def preview_taxpayer_card(tax_id: str, kep_key: str) -> dict:
	_require_manager()
	return fetch_taxpayer_card(tax_id, kep_key)


@frappe.whitelist(methods=["POST"])
def prepare_fop_profile(tax_id: str, kep_key: str, selected_iban: str | None = None) -> dict:
	"""Prepare data for a new, not-yet-saved FOP Profile."""
	_require_manager()
	result = fetch_taxpayer_card(tax_id, kep_key)
	_selected_account(result, selected_iban)
	_ensure_kveds(result)
	return result


@frappe.whitelist(methods=["POST"])
def sync_fop_profile(fop_profile: str, selected_iban: str | None = None) -> dict:
	_require_manager()
	doc = frappe.get_doc("FOP Profile", fop_profile)
	doc.check_permission("write")
	if not doc.cabinet_kep_key:
		frappe.throw(_("Select a KEP key for the Tax Cabinet"))
	result = fetch_taxpayer_card(doc.tax_id, doc.cabinet_kep_key)
	_selected_account(result, selected_iban)
	_ensure_kveds(result)

	for fieldname, value in result["updates"].items():
		if fieldname == "vat_number" or value not in (None, ""):
			doc.set(fieldname, value or None)
	if result.get("kveds"):
		doc.set("kveds", [])
		main = next(item for item in result["kveds"] if item["is_main"])
		doc.kved_main = main["code"]
		for item in result["kveds"]:
			if item["code"] != main["code"]:
				doc.append("kveds", {"kved": item["code"]})
	doc.dps_last_sync_at = frappe.utils.now_datetime()
	doc.dps_last_sync_by = frappe.session.user
	doc.save()
	return {
		"name": doc.name,
		"updates": result["updates"],
		"bank_accounts": result["bank_accounts"],
		"kveds": result["kveds"],
		"synced_at": str(doc.dps_last_sync_at),
	}
