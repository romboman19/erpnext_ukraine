from __future__ import annotations

import json
import frappe

from ukrainian_integrations.utils.logger import log_event


def _guess_customer(phone: str) -> tuple[str | None, str | None]:
    if not phone:
        return None, None
    candidates = [phone, phone.replace('+', ''), phone[-10:]]
    for p in candidates:
        name = frappe.db.get_value('Customer', {'mobile_no': ['like', f'%{p}%']}, 'name')
        if name:
            return 'Customer', name
    return None, None


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

    ref_doctype, ref_name = _guess_customer(from_no if direction == 'inbound' else to_no)

    existing = frappe.db.exists('VitalPBX Call Log', {'call_id': call_id})
    if existing:
        doc = frappe.get_doc('VitalPBX Call Log', existing)
        doc.status = status
        doc.duration_sec = int(payload.get('duration') or doc.duration_sec or 0)
        doc.recording_url = payload.get('recording_url') or doc.recording_url
        doc.raw_payload = json.dumps(payload, ensure_ascii=False)
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({
            'doctype': 'VitalPBX Call Log',
            'direction': direction,
            'status': status,
            'from_number': from_no,
            'to_number': to_no,
            'extension': extension,
            'call_id': call_id,
            'duration_sec': int(payload.get('duration') or 0),
            'recording_url': payload.get('recording_url') or '',
            'reference_doctype': ref_doctype,
            'reference_name': ref_name,
            'raw_payload': json.dumps(payload, ensure_ascii=False),
        })
        doc.insert(ignore_permissions=True)

    log_event('vitalpbx', 'success', f'Webhook {status} call_id:{call_id}', request_payload=payload)
    return {'ok': True, 'call_id': call_id}
