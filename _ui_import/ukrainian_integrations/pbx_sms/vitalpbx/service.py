from __future__ import annotations

import re

import frappe
import requests
from frappe import _

from ukrainian_integrations.pbx_sms.vitalpbx.client import VitalPBXClient
from ukrainian_integrations.utils.logger import log_event
from ukrainian_integrations.utils.operations import mark_operation, require_new_or_return_success, reserve_operation
from ukrainian_integrations.utils.security import SALES_MANAGER_ROLES, SALES_ROLES, permitted_doc, require_roles


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _settings_value(fieldname: str):
    if not frappe.db.exists('DocType', 'VitalPBX Settings'):
        return None
    try:
        doc = frappe.get_single('VitalPBX Settings')
        if fieldname == 'api_key':
            return doc.get_password('api_key', raise_exception=False)
        return doc.get(fieldname)
    except Exception:
        return None


def _client() -> VitalPBXClient:
    if frappe.db.exists('DocType', 'VitalPBX Settings'):
        settings = frappe.get_single('VitalPBX Settings')
        if int(settings.get('enabled') or 0) != 1:
            frappe.throw(_('VitalPBX integration is disabled'))
        base_url = settings.get('base_url')
        api_key = settings.get_password('api_key', raise_exception=False)
        tenant = settings.get('tenant')
    else:
        base_url = _cfg('vitalpbx_base_url')
        api_key = _cfg('vitalpbx_app_key') or _cfg('vitalpbx_api_key')
        tenant = _cfg('vitalpbx_tenant')
    timeout = max(1, min(int(_cfg('vitalpbx_timeout', 20) or 20), 60))
    verify_ssl = int(_cfg('vitalpbx_verify_ssl', 1) or 1) == 1
    if not base_url:
        frappe.throw(_('Не задано vitalpbx_base_url (site_config або VitalPBX Settings)'))
    if not api_key:
        frappe.throw(_('Не задано vitalpbx_api_key (site_config або VitalPBX Settings)'))
    return VitalPBXClient(base_url=base_url, api_key=api_key, timeout=timeout, verify_ssl=verify_ssl, tenant=tenant)


def _mark_call_exception(operation, exc: requests.RequestException) -> None:
    status_code = getattr(getattr(exc, 'response', None), 'status_code', None)
    if status_code is not None and 400 <= int(status_code) < 500:
        mark_operation(operation, 'failed', error=f'VitalPBX HTTP {status_code}')
    else:
        mark_operation(operation, 'unknown', error=frappe.get_traceback())


class PBXRejectedError(frappe.ValidationError):
    pass


def _assert_call_accepted(response) -> None:
    if not isinstance(response, dict) or not response:
        raise RuntimeError(_("VitalPBX returned an ambiguous response"))
    if response.get("ok") is False or response.get("success") is False or response.get("error"):
        raise PBXRejectedError(_("VitalPBX rejected the call request"))
    status = response.get("status")
    status_text = str(status or "").strip().lower()
    accepted = (
        response.get("ok") is True
        or response.get("success") is True
        or status is True
        or status_text in {"accepted", "ok", "queued", "success", "succeeded"}
        or bool(_call_external_id(response))
    )
    if not accepted:
        raise RuntimeError(_("VitalPBX did not provide explicit call acceptance"))


def _call_external_id(response) -> str | None:
    if not isinstance(response, dict):
        return None
    value = response.get('call_id') or response.get('uniqueid') or response.get('id')
    return str(value) if value else None


def _normalize_phone(phone: str) -> str:
    p = ''.join(ch for ch in (phone or '') if ch.isdigit() or ch == '+')
    if p.startswith('0'):
        p = '+38' + p
    if p.startswith('380'):
        p = '+' + p
    return p


def _validated_phone(phone: str, label: str) -> str:
    normalized = _normalize_phone(phone)
    if not re.fullmatch(r"\+[1-9]\d{6,14}", normalized):
        frappe.throw(_("{0} is invalid").format(label))
    return normalized


@frappe.whitelist()
def vitalpbx_healthcheck() -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    try:
        out = _client().health()
        log_event('vitalpbx', 'success', 'Healthcheck OK', response_payload=out)
        return {'ok': True, 'response': out}
    except Exception:
        log_event('vitalpbx', 'error', 'Healthcheck failed', error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def click_to_call(extension: str, destination: str, idempotency_key: str) -> dict:
    require_roles(*SALES_ROLES)
    if not (idempotency_key or '').strip():
        frappe.throw(_('idempotency_key is required'))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", str(extension or "")):
        frappe.throw(_('Extension is required'))
    dst = _validated_phone(destination, _("Destination phone"))

    roles = set(frappe.get_roles(frappe.session.user))
    if not roles.intersection(SALES_MANAGER_ROLES):
        own_extension = (frappe.db.get_value('User', frappe.session.user, 'vitalpbx_extension') or '').strip()
        if not own_extension or own_extension != str(extension).strip():
            frappe.throw(_('You may only call from your assigned VitalPBX extension'), frappe.PermissionError)

    req = {'extension': extension, 'destination': dst}
    reservation = reserve_operation(
        idempotency_key=f'vitalpbx:click_to_call:{idempotency_key}',
        integration='vitalpbx',
        operation_type='click_to_call',
        request_payload=req,
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return cached
    client = _client()
    log_event('vitalpbx', 'queued', 'Click2Call request', request_payload=req)
    mark_operation(reservation.doc, 'unknown', response_payload={'phase': 'external_request_in_progress'})
    try:
        out = client.click_to_call(extension=extension, destination=dst)
        _assert_call_accepted(out)
        result = {'ok': True, 'response': out}
        mark_operation(
            reservation.doc,
            'succeeded',
            external_id=_call_external_id(out),
            response_payload=result,
        )
        log_event('vitalpbx', 'success', 'Click2Call success', request_payload=req, response_payload=out)
        return result
    except requests.RequestException as exc:
        _mark_call_exception(reservation.doc, exc)
        log_event('vitalpbx', 'error', 'Click2Call failed', request_payload=req, error_trace=frappe.get_traceback())
        raise
    except PBXRejectedError:
        mark_operation(reservation.doc, 'failed', error=frappe.get_traceback())
        log_event('vitalpbx', 'error', 'Click2Call rejected', request_payload=req, error_trace=frappe.get_traceback())
        raise
    except Exception:
        mark_operation(reservation.doc, 'unknown', error=frappe.get_traceback())
        log_event('vitalpbx', 'error', 'Click2Call outcome unknown', request_payload=req, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def click_to_call_customer(customer: str, idempotency_key: str, extension: str | None = None) -> dict:
    require_roles(*SALES_ROLES)
    if not customer:
        frappe.throw(_('Customer is required'))
    if not extension:
        extension = (frappe.db.get_value('User', frappe.session.user, 'vitalpbx_extension') or _settings_value('default_extension') or '').strip()
    if not extension:
        frappe.throw(_('Не задано extension (у користувача або VitalPBX Settings)'))
    c = permitted_doc('Customer', customer, 'read')
    phone = c.get('mobile_no') or c.get('phone')
    if not phone:
        frappe.throw(_('У клієнта не заповнений телефон'))
    return click_to_call(extension=extension, destination=phone, idempotency_key=idempotency_key)


@frappe.whitelist()
def dialer_call(
    number: str,
    cos_id: int,
    destination_category_id: int,
    destination_id: int,
    idempotency_key: str,
    cid_number: str | None = None,
    cid_name: str | None = None,
    timeout: int | None = None,
) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    if not (idempotency_key or '').strip():
        frappe.throw(_('idempotency_key is required'))
    if not number:
        frappe.throw(_('Number is required'))

    normalized = _validated_phone(number, _("Dialer number"))
    if normalized.startswith('+'):
        normalized = normalized[1:]

    ids = (int(cos_id), int(destination_category_id), int(destination_id))
    if any(value <= 0 for value in ids):
        frappe.throw(_("Dialer IDs must be positive integers"))
    if timeout is not None and not 1 <= int(timeout) <= 300:
        frappe.throw(_("Dialer timeout must be between 1 and 300 seconds"))

    req = {
        'number': normalized,
        'cos_id': int(cos_id),
        'destination_category_id': int(destination_category_id),
        'destination_id': int(destination_id),
        'cid_number': cid_number,
        'cid_name': cid_name,
        'timeout': timeout,
    }

    reservation = reserve_operation(
        idempotency_key=f'vitalpbx:dialer_call:{idempotency_key}',
        integration='vitalpbx',
        operation_type='dialer_call',
        request_payload=req,
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return cached
    client = _client()

    log_event('vitalpbx', 'queued', 'Dialer call request', request_payload=req)
    mark_operation(reservation.doc, 'unknown', response_payload={'phase': 'external_request_in_progress'})
    try:
        out = client.dialer_call(
            number=normalized,
            cos_id=int(cos_id),
            destination_category_id=int(destination_category_id),
            destination_id=int(destination_id),
            cid_number=cid_number,
            cid_name=cid_name,
            timeout=timeout,
        )
        _assert_call_accepted(out)
        result = {'ok': True, 'response': out}
        mark_operation(
            reservation.doc,
            'succeeded',
            external_id=_call_external_id(out),
            response_payload=result,
        )
        log_event('vitalpbx', 'success', 'Dialer call queued', request_payload=req, response_payload=out)
        return result
    except requests.RequestException as exc:
        _mark_call_exception(reservation.doc, exc)
        log_event('vitalpbx', 'error', 'Dialer call failed', request_payload=req, error_trace=frappe.get_traceback())
        raise
    except PBXRejectedError:
        mark_operation(reservation.doc, 'failed', error=frappe.get_traceback())
        log_event('vitalpbx', 'error', 'Dialer call rejected', request_payload=req, error_trace=frappe.get_traceback())
        raise
    except Exception:
        mark_operation(reservation.doc, 'unknown', error=frappe.get_traceback())
        log_event('vitalpbx', 'error', 'Dialer call outcome unknown', request_payload=req, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def get_default_extension() -> dict:
    require_roles(*SALES_ROLES)
    ext = frappe.db.get_value('User', frappe.session.user, 'vitalpbx_extension') or ''
    if not ext:
        ext = _settings_value('default_extension') or ''
    return {'ok': True, 'extension': ext}
