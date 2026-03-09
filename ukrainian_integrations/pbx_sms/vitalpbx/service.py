from __future__ import annotations

import frappe
from frappe import _

from ukrainian_integrations.pbx_sms.vitalpbx.client import VitalPBXClient
from ukrainian_integrations.utils.logger import log_event


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _settings_value(fieldname: str):
    if not frappe.db.exists('DocType', 'VitalPBX Settings'):
        return None
    try:
        return frappe.db.get_single_value('VitalPBX Settings', fieldname)
    except Exception:
        return None


def _client() -> VitalPBXClient:
    base_url = _cfg('vitalpbx_base_url') or _settings_value('base_url')
    api_key = _cfg('vitalpbx_app_key') or _cfg('vitalpbx_app_key') or _cfg('vitalpbx_api_key') or _settings_value('api_key')
    timeout = _cfg('vitalpbx_timeout', 20)
    verify_ssl = int(_cfg('vitalpbx_verify_ssl', 1) or 1) == 1
    tenant = _cfg('vitalpbx_tenant') or _settings_value('tenant')
    if not base_url:
        frappe.throw(_('Не задано vitalpbx_base_url (site_config або VitalPBX Settings)'))
    if not api_key:
        frappe.throw(_('Не задано vitalpbx_api_key (site_config або VitalPBX Settings)'))
    return VitalPBXClient(base_url=base_url, api_key=api_key, timeout=timeout, verify_ssl=verify_ssl, tenant=tenant)


def _normalize_phone(phone: str) -> str:
    p = ''.join(ch for ch in (phone or '') if ch.isdigit() or ch == '+')
    if p.startswith('0'):
        p = '+38' + p
    if p.startswith('380'):
        p = '+' + p
    return p


@frappe.whitelist()
def vitalpbx_healthcheck() -> dict:
    try:
        out = _client().health()
        log_event('vitalpbx', 'success', 'Healthcheck OK', response_payload=out)
        return {'ok': True, 'response': out}
    except Exception:
        log_event('vitalpbx', 'error', 'Healthcheck failed', error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def click_to_call(extension: str, destination: str) -> dict:
    if not extension:
        frappe.throw(_('Extension is required'))
    dst = _normalize_phone(destination)
    if not dst:
        frappe.throw(_('Destination phone is invalid'))

    req = {'extension': extension, 'destination': dst}
    log_event('vitalpbx', 'queued', 'Click2Call request', request_payload=req)
    try:
        out = _client().click_to_call(extension=extension, destination=dst)
        log_event('vitalpbx', 'success', 'Click2Call success', request_payload=req, response_payload=out)
        return {'ok': True, 'response': out}
    except Exception:
        log_event('vitalpbx', 'error', 'Click2Call failed', request_payload=req, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def click_to_call_customer(customer: str, extension: str) -> dict:
    if not customer:
        frappe.throw(_('Customer is required'))
    c = frappe.get_doc('Customer', customer)
    phone = c.get('mobile_no') or c.get('phone')
    if not phone:
        frappe.throw(_('У клієнта не заповнений телефон'))
    return click_to_call(extension=extension, destination=phone)


@frappe.whitelist()
def dialer_call(
    number: str,
    cos_id: int,
    destination_category_id: int,
    destination_id: int,
    cid_number: str | None = None,
    cid_name: str | None = None,
    timeout: int | None = None,
) -> dict:
    if not number:
        frappe.throw(_('Number is required'))

    normalized = _normalize_phone(number)
    if normalized.startswith('+'):
        normalized = normalized[1:]

    req = {
        'number': normalized,
        'cos_id': int(cos_id),
        'destination_category_id': int(destination_category_id),
        'destination_id': int(destination_id),
        'cid_number': cid_number,
        'cid_name': cid_name,
        'timeout': timeout,
    }

    log_event('vitalpbx', 'queued', 'Dialer call request', request_payload=req)
    try:
        out = _client().dialer_call(
            number=normalized,
            cos_id=int(cos_id),
            destination_category_id=int(destination_category_id),
            destination_id=int(destination_id),
            cid_number=cid_number,
            cid_name=cid_name,
            timeout=timeout,
        )
        log_event('vitalpbx', 'success', 'Dialer call queued', request_payload=req, response_payload=out)
        return {'ok': True, 'response': out}
    except Exception:
        log_event('vitalpbx', 'error', 'Dialer call failed', request_payload=req, error_trace=frappe.get_traceback())
        raise
