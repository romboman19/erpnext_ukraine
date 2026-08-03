from __future__ import annotations

import frappe
from frappe import _

from erpnext_ua.ua_pos.adapters.terminal import PrivatPosAdapter, PrivatPOSGatewayClient


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
	return PrivatPosAdapter(PrivatPOSGatewayClient(**cfg))


def resolve_terminal(terminal: str) -> dict:
	doc = frappe.get_doc("PB POS Terminal", terminal)
	if not doc.is_active:
		frappe.throw(_("Термінал {0} неактивний").format(terminal))
	return {"name": doc.name, "ip": doc.ip_address, "port": int(doc.tcp_port or 2000)}


@frappe.whitelist(methods=["POST"])
def test_connection(terminal: str) -> dict:
	frappe.only_for(("System Manager", "POS Administrator"))
	return {"ok": get_adapter().ping(resolve_terminal(terminal))}


@frappe.whitelist(methods=["POST"])
def test_payment(terminal: str, amount: float = 1) -> dict:
	frappe.only_for(("System Manager", "POS Administrator"))
	operation_id = f"TEST-SALE-{frappe.generate_hash(length=12)}"
	result = get_adapter().sale(resolve_terminal(terminal), float(amount), operation_id)
	return {"operation_id": operation_id, **result.as_dict()}


@frappe.whitelist(methods=["POST"])
def test_refund(terminal: str, amount: float, reference: str) -> dict:
	frappe.only_for(("System Manager", "POS Administrator"))
	operation_id = f"TEST-REFUND-{frappe.generate_hash(length=12)}"
	result = get_adapter().refund(resolve_terminal(terminal), float(amount), operation_id, reference)
	return {"operation_id": operation_id, **result.as_dict()}


@frappe.whitelist(methods=["POST"])
def operation_status(terminal: str, operation_id: str) -> dict:
	frappe.only_for(("System Manager", "POS Administrator"))
	return get_adapter().status(resolve_terminal(terminal), operation_id).as_dict()
