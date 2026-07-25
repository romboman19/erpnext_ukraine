from __future__ import annotations

import re

import frappe
from frappe import _

from erpnext_ua.ua_pos.adapters.terminal import PrivatPosAdapter, PrivatPOSGatewayClient

_TERMINAL_FISCAL_FIELDS = {
	"merchant_id": "Ідентифікатор торговця",
	"device_id": "Ідентифікатор платіжного пристрою",
	"payment_system_name": "Платіжна система",
	"payment_system_tax_number": "Податковий номер платіжної системи",
	"acquirer_name": "Найменування еквайра",
	"acquirer_tax_number": "Податковий номер еквайра",
}

_TERMINAL_INFO_ALIASES = {
	"merchant_id": {"merchantid", "merchantidentifier", "merchantnumber", "acquireid"},
	"device_id": {"deviceid", "terminalid", "terminalidentifier", "serialnum", "serialnumber", "posid"},
	"payment_system_name": {
		"paymentsystemname", "paymentsystem", "paysystemname", "paysystem",
		"cardsystem", "cardbrand", "scheme",
	},
	"payment_system_tax_number": {
		"paymentsystemtaxnumber", "paysystemtaxnumber", "paysystaxnumber",
	},
	"acquirer_name": {"acquirername", "acquirenm", "acquirename", "acquiringbank", "acquirerbankname"},
	"acquirer_tax_number": {
		"acquirertaxnumber", "acquirerpn", "acquirepn", "acquirerokpo", "acquireokpo",
	},
}

_PAYMENT_SYSTEM_CONTEXTS = {"paysys", "paymentsystem", "paymentsysteminfo", "paymentsystemdata"}

_TECHNICAL_FIELDS = {
	"terminal_vendor": "Vendor",
	"terminal_model": "Model",
	"software_version": "Версія ПЗ",
	"terminal_profile_id": "Profile ID",
	"terminal_serial_number": "Серійний номер",
	"acquirer_profiles": "Профілі/еквайри",
}


def _normalized_info_key(value: object) -> str:
	return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _terminal_info_scalars(value, path=(), depth=0):
	if depth > 8:
		return
	if isinstance(value, dict):
		for key, child in value.items():
			yield from _terminal_info_scalars(child, (*path, _normalized_info_key(key)), depth + 1)
	elif isinstance(value, list):
		for child in value[:100]:
			yield from _terminal_info_scalars(child, path, depth + 1)
	elif value is not None and not isinstance(value, bool):
		text = str(value).strip()
		if text:
			yield path, text[:140]


def _terminal_info_values(response: dict) -> dict[str, str]:
	values: dict[str, str] = {}
	scalars = list(_terminal_info_scalars(response))
	for fieldname, aliases in _TERMINAL_INFO_ALIASES.items():
		for path, value in scalars:
			if path and path[-1] in aliases:
				values[fieldname] = value
				break

	# Fiscal XML-compatible responses use generic NAME/TAXNUM inside a PAYSYS block.
	for path, value in scalars:
		if not path or not any(part in _PAYMENT_SYSTEM_CONTEXTS for part in path[:-1]):
			continue
		if path[-1] == "name" and "payment_system_name" not in values:
			values["payment_system_name"] = value
		elif path[-1] == "taxnum" and "payment_system_tax_number" not in values:
			values["payment_system_tax_number"] = value
	return values


def _technical_terminal_values(info: dict, identity: dict | None = None) -> dict[str, str]:
	values: dict[str, str] = {}
	identity_params = (identity or {}).get("params") or {}
	if isinstance(identity_params, dict):
		vendor = str(identity_params.get("vendor") or "").strip()
		model = str(identity_params.get("model") or "").strip()
		if vendor:
			values["terminal_vendor"] = vendor[:140]
		if model:
			values["terminal_model"] = model[:140]

	params = info.get("params") or {}
	version = str(params.get("version") or "").strip() if isinstance(params, dict) else ""
	if not version:
		return values

	parts = version.split()
	values["software_version"] = parts[0][:140]
	profile_serial = parts[1] if len(parts) > 1 else ""
	profile_serial, *acquirers = profile_serial.split("/") if profile_serial else [""]
	# Official BPOS format: 10-char terminal profile ID + 10-char POS serial number.
	if len(profile_serial) >= 20:
		values["terminal_profile_id"] = profile_serial[:10]
		values["terminal_serial_number"] = profile_serial[10:20]
		values.setdefault("device_id", profile_serial[10:20])
	elif profile_serial:
		values["terminal_profile_id"] = profile_serial[:140]
	if acquirers:
		values["acquirer_profiles"] = "\n".join(item[:140] for item in acquirers if item)[:2000]
	return values


def _settings() -> dict:
	settings = frappe.get_single("PB POS Settings")
	return {
		"gateway_url": (settings.gateway_url or frappe.conf.get("pb_pos_gateway_url") or "").strip(),
		"api_key": (
			settings.get_password("api_key", raise_exception=False) or frappe.conf.get("pb_pos_api_key") or ""
		).strip(),
		"timeout": int(settings.request_timeout_sec or frappe.conf.get("pb_pos_timeout") or 20),
	}


def get_adapter() -> PrivatPosAdapter:
	cfg = _settings()
	if not cfg["gateway_url"] or not cfg["api_key"]:
		frappe.throw(_("Налаштуйте URL та API-ключ у PB POS Settings"))
	return PrivatPosAdapter(
		PrivatPOSGatewayClient(
			base_url=cfg["gateway_url"],
			api_key=cfg["api_key"],
			timeout=cfg["timeout"],
		)
	)


def resolve_terminal(terminal: str, *, require_active: bool = True) -> dict:
	doc = frappe.get_doc("PB POS Terminal", terminal)
	if require_active and not doc.is_active:
		frappe.throw(_("Термінал {0} неактивний").format(terminal))
	if not (doc.ip_address or "").strip():
		frappe.throw(_("Вкажіть IP-адресу термінала {0}").format(terminal))
	return {"name": doc.name, "ip": doc.ip_address, "port": int(doc.tcp_port or 2000)}


@frappe.whitelist()
def load_terminal_data(terminal: str) -> dict:
	frappe.only_for(("System Manager", "POS Administrator"))
	doc = frappe.get_doc("PB POS Terminal", terminal)
	resolved = resolve_terminal(terminal, require_active=False)
	adapter = get_adapter()
	response = adapter.terminal_info(resolved)
	if response.get("error") or int(response.get("_http_status") or 200) >= 400:
		description = response.get("description") or response.get("errorDescription") or _(
			"Невідома помилка gateway"
		)
		frappe.throw(_("Не вдалося завантажити дані термінала: {0}").format(description))

	identity = adapter.identify(resolved)
	identity_warning = ""
	if identity.get("error") or int(identity.get("_http_status") or 200) >= 400:
		identity_warning = str(
			identity.get("description") or identity.get("errorDescription") or "Identify failed"
		)[:300]
		identity = {}

	loaded = {**_terminal_info_values(response), **_technical_terminal_values(response, identity)}
	for fieldname, value in loaded.items():
		doc.set(fieldname, value)
	if loaded:
		doc.save()

	missing = [fieldname for fieldname in _TERMINAL_FISCAL_FIELDS if not doc.get(fieldname)]
	return {
		"ok": True,
		"updated": list(loaded),
		"missing": missing,
		"updated_labels": [
			(_TERMINAL_FISCAL_FIELDS | _TECHNICAL_FIELDS).get(fieldname, fieldname) for fieldname in loaded
		],
		"missing_labels": [_TERMINAL_FISCAL_FIELDS[fieldname] for fieldname in missing],
		"technical": {
			fieldname: doc.get(fieldname) for fieldname in _TECHNICAL_FIELDS if doc.get(fieldname)
		},
		"identity_warning": identity_warning,
	}


@frappe.whitelist()
def test_connection(terminal: str) -> dict:
	frappe.only_for(("System Manager", "POS Administrator"))
	return {"ok": get_adapter().ping(resolve_terminal(terminal))}


@frappe.whitelist()
def test_payment(terminal: str, amount: float = 1) -> dict:
	frappe.only_for(("System Manager", "POS Administrator"))
	operation_id = f"TEST-SALE-{frappe.generate_hash(length=12)}"
	result = get_adapter().sale(resolve_terminal(terminal), float(amount), operation_id)
	return {"operation_id": operation_id, **result.as_dict()}


@frappe.whitelist()
def test_refund(terminal: str, amount: float, reference: str) -> dict:
	frappe.only_for(("System Manager", "POS Administrator"))
	operation_id = f"TEST-REFUND-{frappe.generate_hash(length=12)}"
	result = get_adapter().refund(resolve_terminal(terminal), float(amount), operation_id, reference)
	return {"operation_id": operation_id, **result.as_dict()}


@frappe.whitelist()
def operation_status(terminal: str, operation_id: str) -> dict:
	frappe.only_for(("System Manager", "POS Administrator"))
	return get_adapter().status(resolve_terminal(terminal), operation_id).as_dict()
