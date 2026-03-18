from __future__ import annotations

import json
import frappe

from ukrainian_integrations.utils.logger import log_event


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
        return int(frappe.db.get_single_value('VitalPBX Settings', fieldname) or default)
    except Exception:
        return int(default)


def _guess_customer(phone: str):
    if not phone:
        return None
    p = _normalize_phone(phone)
    variants = [v for v in {p, p.replace('+', ''), p[-10:], phone} if v]
    for v in variants:
        name = frappe.db.get_value('Customer', {'mobile_no': ['like', f'%{v}%']}, 'name')
        if name:
            return frappe.get_doc('Customer', name)
    for v in variants:
        name = frappe.db.get_value('Customer', {'phone': ['like', f'%{v}%']}, 'name')
        if name:
            return frappe.get_doc('Customer', name)
    return None


def _recent_sales(customer_name: str, limit: int = 5):
    if not customer_name:
        return []
    rows = frappe.get_all('Sales Invoice', filters={'customer': customer_name, 'docstatus': ['<', 2]}, fields=['name','posting_date','rounded_total','grand_total','status'], order_by='posting_date desc, creation desc', limit_page_length=limit)
    return [{'name': r.name, 'posting_date': str(r.posting_date) if r.posting_date else '', 'total': float(r.rounded_total or r.grand_total or 0), 'status': r.status} for r in rows]


def _target_users(extension: str):
    ext = (extension or '').strip()
    if not ext:
        return []
    return frappe.get_all('User', filters={'enabled': 1, 'vitalpbx_extension': ext}, fields=['name','full_name'])


def _publish_popup(payload: dict, extension: str):
    if _settings_enabled('popup_enabled', 1) != 1:
        return
    users = _target_users(extension)
    for u in users:
        frappe.publish_realtime('vitalpbx_call_popup', payload, user=u.name, after_commit=True)


@frappe.whitelist(allow_guest=True)
def webhook_event():
    payload = frappe.request.get_json(silent=True) or {}
    call_id = str(payload.get('call_id') or payload.get('linkedid') or payload.get('uniqueid') or '').strip()
    if not call_id:
        frappe.local.response['http_status_code'] = 400
        return {'ok': False, 'error': 'missing_call_id'}

    direction = (payload.get('direction') or 'inbound').lower()
    status = (payload.get('status') or payload.get('call_status') or 'ringing').lower()
    from_no = str(payload.get('from') or payload.get('caller') or '').strip()
    to_no = str(payload.get('to') or payload.get('destination') or '').strip()
    extension = str(payload.get('extension') or '').strip()

    customer = _guess_customer(from_no if direction == 'inbound' else to_no)
    ref_doctype = 'Customer' if customer else None
    ref_name = customer.name if customer else None

    existing = frappe.db.exists('VitalPBX Call Log', {'call_id': call_id})
    if existing:
        doc = frappe.get_doc('VitalPBX Call Log', existing)
        doc.status = status
        doc.duration_sec = int(payload.get('duration') or doc.duration_sec or 0)
        doc.recording_url = payload.get('recording_url') or doc.recording_url
        doc.raw_payload = json.dumps(payload, ensure_ascii=False)
        if not doc.reference_name and ref_name:
            doc.reference_doctype = ref_doctype
            doc.reference_name = ref_name
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({'doctype':'VitalPBX Call Log','direction':direction,'status':status,'from_number':from_no,'to_number':to_no,'extension':extension,'call_id':call_id,'duration_sec':int(payload.get('duration') or 0),'recording_url': payload.get('recording_url') or '','reference_doctype':ref_doctype,'reference_name':ref_name,'raw_payload':json.dumps(payload, ensure_ascii=False)})
        doc.insert(ignore_permissions=True)

    popup_payload = {'call_id': call_id, 'direction': direction, 'status': status, 'from_number': from_no, 'to_number': to_no, 'extension': extension, 'customer': {'name': customer.name, 'customer_name': customer.customer_name, 'mobile_no': customer.mobile_no, 'phone': customer.phone} if customer else None, 'recent_sales_invoices': _recent_sales(customer.name if customer else None, limit=5), 'call_log': doc.name}
    _publish_popup(popup_payload, extension)

    log_event('vitalpbx', 'success', f'Webhook {status} call_id:{call_id}', request_payload=payload)
    return {'ok': True, 'call_id': call_id, 'call_log': doc.name}
