from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import frappe

from ukrainian_integrations.utils.logger import log_event, sanitize_payload
from ukrainian_integrations.utils.security import SALES_ROLES, permitted_doc, require_roles, secrets_equal

STATUS_RANK = {
    'ringing': 10,
    'answered': 20,
    'missed': 30,
    'failed': 30,
    'completed': 40,
}


def _normalize_phone(phone: str) -> str:
    p = ''.join(ch for ch in (phone or '') if ch.isdigit() or ch == '+')
    if p.startswith('0'):
        p = '+38' + p
    if p.startswith('380'):
        p = '+' + p
    return p


def _settings_enabled(fieldname: str, default=1) -> int:
    if not frappe.db.exists('DocType', 'VitalPBX Settings'):
        return int(default)
    try:
        value = frappe.db.get_single_value('VitalPBX Settings', fieldname)
        return int(default if value is None else value)
    except Exception:
        return int(default)


def _guess_customer(phone: str):
    if not phone:
        return None
    p = _normalize_phone(phone)
    local = p[-10:] if len(p) >= 10 else p
    variants = [v for v in {p, p.replace('+', ''), local, f"0{local[-9:]}" if len(local) >= 9 else local, phone} if v]
    for v in variants:
        name = frappe.db.get_value('Customer', {'mobile_no': v}, 'name')
        if name:
            return frappe.get_doc('Customer', name)
    for v in variants:
        name = frappe.db.get_value('Customer', {'phone': v}, 'name')
        if name:
            return frappe.get_doc('Customer', name)
    return None


def _target_users(extension: str):
    ext = (extension or '').strip()
    if not ext:
        return []
    return frappe.get_all('User', filters={'enabled': 1, 'vitalpbx_extension': ext}, fields=['name','full_name'])


def get_permission_query_conditions(user: str | None = None) -> str | None:
    user = user or getattr(getattr(frappe, 'session', None), 'user', None)
    roles = set(frappe.get_roles(user))
    if user == 'Administrator' or roles.intersection({'System Manager', 'Sales Manager'}):
        return None
    if 'Sales User' not in roles:
        return '1=0'
    extension = (frappe.db.get_value('User', user, 'vitalpbx_extension') or '').strip()
    if not extension:
        return '1=0'
    return f"`tabVitalPBX Call Log`.`extension` = {frappe.db.escape(extension)}"


def has_permission(doc, user: str | None = None, permission_type: str | None = None) -> bool:
    del permission_type
    user = user or getattr(getattr(frappe, 'session', None), 'user', None)
    roles = set(frappe.get_roles(user))
    if user == 'Administrator' or roles.intersection({'System Manager', 'Sales Manager'}):
        return True
    extension = (frappe.db.get_value('User', user, 'vitalpbx_extension') or '').strip()
    return bool('Sales User' in roles and extension and extension == str(doc.extension or '').strip())


def is_status_transition_allowed(old_status: str, new_status: str) -> bool:
    return new_status == old_status or STATUS_RANK.get(new_status, 0) > STATUS_RANK.get(old_status, 0)


def _apply_call_event(doc, *, status: str, duration: int, recording_url: str, raw_payload: str, ref_doctype, ref_name):
    old_status = str(doc.status or '')
    if is_status_transition_allowed(old_status, status):
        doc.status = status
        doc.raw_payload = raw_payload
    doc.duration_sec = max(int(doc.duration_sec or 0), duration)
    doc.recording_url = recording_url or doc.recording_url
    if not doc.reference_name and ref_name:
        doc.reference_doctype = ref_doctype
        doc.reference_name = ref_name
    doc.save(ignore_permissions=True)
    return doc


def _lock_call_log(name: str) -> None:
    frappe.db.sql("SELECT name FROM `tabVitalPBX Call Log` WHERE name = %s FOR UPDATE", (name,))




def _get_webhook_key() -> str:
    # priority: site_config, then VitalPBX Settings.webhook_key (if field exists)
    key = (frappe.conf.get('vitalpbx_webhook_key') or '').strip()
    if key:
        return key
    if frappe.db.exists('DocType', 'VitalPBX Settings'):
        try:
            settings = frappe.get_single('VitalPBX Settings')
            key = (settings.get_password('webhook_key', raise_exception=False) or '').strip()
            if key:
                return key
        except Exception:
            frappe.logger('ukrainian_integrations').warning(
                'VitalPBX webhook key could not be read from settings',
                exc_info=True,
            )
    return ''


def _validate_webhook_request() -> None:
    if _settings_enabled('enabled', 0) != 1:
        frappe.throw('VitalPBX integration is disabled', frappe.PermissionError)
    expected = _get_webhook_key()
    if not expected:
        frappe.throw('VitalPBX webhook key is not configured', frappe.PermissionError)

    supplied = (
        frappe.get_request_header('X-Webhook-Key')
        or frappe.get_request_header('X-VitalPBX-Key')
        or ''
    ).strip()
    if not supplied and int(frappe.conf.get('vitalpbx_allow_query_key', 0) or 0) == 1:
        supplied = (frappe.request.args.get('key') if frappe.request else '') or ''
        supplied = supplied.strip()

    if not secrets_equal(supplied, expected):
        frappe.throw('Unauthorized webhook request', frappe.PermissionError)


def _publish_popup(payload: dict, extension: str):
    if _settings_enabled('popup_enabled', 1) != 1:
        return
    users = _target_users(extension)
    for u in users:
        frappe.publish_realtime('vitalpbx_call_popup', payload, user=u.name, after_commit=True)


@frappe.whitelist(allow_guest=True)
def webhook_event():
    _validate_webhook_request()
    content_length = getattr(frappe.request, 'content_length', 0) or 0
    if int(content_length) > 65_536:
        frappe.local.response['http_status_code'] = 413
        return {'ok': False, 'error': 'payload_too_large'}
    payload = frappe.request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        frappe.local.response['http_status_code'] = 400
        return {'ok': False, 'error': 'invalid_json_object'}
    call_id = str(payload.get('call_id') or payload.get('linkedid') or payload.get('uniqueid') or '').strip()
    if not call_id:
        frappe.local.response['http_status_code'] = 400
        return {'ok': False, 'error': 'missing_call_id'}
    if len(call_id) > 140:
        frappe.local.response['http_status_code'] = 400
        return {'ok': False, 'error': 'invalid_call_id'}

    direction = (payload.get('direction') or 'inbound').lower()
    status = (payload.get('status') or payload.get('call_status') or 'ringing').lower()
    if direction not in {'inbound', 'outbound'} or status not in {'ringing', 'answered', 'missed', 'failed', 'completed'}:
        frappe.local.response['http_status_code'] = 400
        return {'ok': False, 'error': 'invalid_direction_or_status'}
    from_no = str(payload.get('from') or payload.get('caller') or '').strip()
    to_no = str(payload.get('to') or payload.get('destination') or '').strip()
    extension = str(payload.get('extension') or '').strip()
    if len(from_no) > 64 or len(to_no) > 64 or len(extension) > 32:
        frappe.local.response['http_status_code'] = 400
        return {'ok': False, 'error': 'invalid_phone_or_extension'}
    if extension and not re.fullmatch(r'[A-Za-z0-9_.-]+', extension):
        frappe.local.response['http_status_code'] = 400
        return {'ok': False, 'error': 'invalid_extension'}
    try:
        duration = max(0, min(int(payload.get('duration') or 0), 86400))
    except (TypeError, ValueError):
        frappe.local.response['http_status_code'] = 400
        return {'ok': False, 'error': 'invalid_duration'}
    recording_url = str(payload.get('recording_url') or '').strip()
    if recording_url:
        parsed_recording = urlparse(recording_url)
        allowed_schemes = {'https'}
        if int(frappe.conf.get('vitalpbx_allow_insecure_recording_url', 0) or 0) == 1:
            allowed_schemes.add('http')
        if (
            len(recording_url) > 1000
            or parsed_recording.scheme not in allowed_schemes
            or not parsed_recording.hostname
            or parsed_recording.username
            or parsed_recording.password
        ):
            recording_url = ''
    raw_payload = json.dumps(sanitize_payload(payload), ensure_ascii=False, default=str)[:20000]

    customer = _guess_customer(from_no if direction == 'inbound' else to_no)
    ref_doctype = 'Customer' if customer else None
    ref_name = customer.name if customer else None

    existing = frappe.db.exists('VitalPBX Call Log', {'call_id': call_id})
    if existing:
        _lock_call_log(existing)
        doc = frappe.get_doc('VitalPBX Call Log', existing)
        doc = _apply_call_event(
            doc,
            status=status,
            duration=duration,
            recording_url=recording_url,
            raw_payload=raw_payload,
            ref_doctype=ref_doctype,
            ref_name=ref_name,
        )
    else:
        doc = frappe.get_doc({'doctype':'VitalPBX Call Log','direction':direction,'status':status,'from_number':from_no,'to_number':to_no,'extension':extension,'call_id':call_id,'duration_sec':duration,'recording_url':recording_url,'reference_doctype':ref_doctype,'reference_name':ref_name,'raw_payload':raw_payload})
        frappe.db.savepoint('vitalpbx_call_log_insert')
        try:
            doc.insert(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            frappe.db.rollback(save_point='vitalpbx_call_log_insert')
            existing = frappe.db.exists('VitalPBX Call Log', {'call_id': call_id})
            if not existing:
                raise
            _lock_call_log(existing)
            doc = _apply_call_event(
                frappe.get_doc('VitalPBX Call Log', existing),
                status=status,
                duration=duration,
                recording_url=recording_url,
                raw_payload=raw_payload,
                ref_doctype=ref_doctype,
                ref_name=ref_name,
            )

    popup_payload = {'call_id': call_id, 'direction': doc.direction, 'status': doc.status, 'from_number': doc.from_number, 'to_number': doc.to_number, 'extension': doc.extension, 'call_log': doc.name}
    _publish_popup(popup_payload, doc.extension)

    log_event('vitalpbx', 'success', f'Webhook {status} call_id:{call_id}', request_payload={'call_id': call_id, 'direction': direction, 'status': status, 'extension': extension})
    return {'ok': True, 'call_id': call_id, 'call_log': doc.name, 'status': doc.status}


@frappe.whitelist()
def get_call_context(call_id: str) -> dict:
    require_roles(*SALES_ROLES)
    name = frappe.db.get_value('VitalPBX Call Log', {'call_id': call_id}, 'name')
    if not name:
        frappe.throw('Call log not found')
    call_log = permitted_doc('VitalPBX Call Log', name, 'read')
    customer = None
    recent = []
    if call_log.reference_doctype == 'Customer' and call_log.reference_name:
        customer_doc = permitted_doc('Customer', call_log.reference_name, 'read')
        customer = {'name': customer_doc.name, 'customer_name': customer_doc.customer_name, 'mobile_no': customer_doc.mobile_no, 'phone': customer_doc.phone}
        rows = frappe.get_list('Sales Invoice', filters={'customer': customer_doc.name, 'docstatus': ['<', 2]}, fields=['name', 'posting_date', 'rounded_total', 'grand_total', 'status'], order_by='posting_date desc, creation desc', limit_page_length=5)
        recent = [{'name': r.name, 'posting_date': str(r.posting_date) if r.posting_date else '', 'total': float(r.rounded_total or r.grand_total or 0), 'status': r.status} for r in rows]
    return {'ok': True, 'customer': customer, 'recent_sales_invoices': recent}
