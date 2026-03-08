from __future__ import annotations

import requests
import frappe
from frappe import _

from ukrainian_integrations.utils.logger import log_event

TURBOSMS_URL_DEFAULT = 'https://api.turbosms.ua/message/send.json'


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _normalize_phone(phone: str) -> str:
    p = ''.join(ch for ch in (phone or '') if ch.isdigit() or ch == '+')
    if p.startswith('0'):
        p = '+38' + p
    if p.startswith('380'):
        p = '+' + p
    return p


@frappe.whitelist()
def send_sms(phone: str, text: str, sender: str | None = None) -> dict:
    token = (_cfg('turbosms_token') or '').strip()
    if not token:
        frappe.throw(_('Не задано turbosms_token у site_config.json'))

    to = _normalize_phone(phone)
    if not to:
        frappe.throw(_('Некоректний номер телефону'))

    sender_name = (sender or _cfg('turbosms_sender') or 'HUNTER RV').strip()
    url = (_cfg('turbosms_url') or TURBOSMS_URL_DEFAULT).strip()

    payload = {
        'recipients': [to],
        'sms': {
            'sender': sender_name,
            'text': text,
        },
    }
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }

    log_event('turbosms', 'queued', f'Send SMS to {to}', request_payload={'phone': to, 'sender': sender_name})

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=25)
        data = resp.json() if (resp.text or '').strip() else {}
        if resp.status_code >= 400:
            log_event(
                'turbosms',
                'error',
                f'HTTP {resp.status_code}',
                request_payload=payload,
                response_payload=data or {'text': (resp.text or '')[:1000]},
            )
            frappe.throw(_('TurboSMS помилка: HTTP {0}').format(resp.status_code))

        log_event('turbosms', 'success', f'SMS sent to {to}', request_payload=payload, response_payload=data)
        return {'ok': True, 'phone': to, 'response': data}
    except Exception:
        log_event('turbosms', 'error', f'SMS send failed to {to}', request_payload=payload, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def send_sms_to_customer(customer: str, text: str, sender: str | None = None) -> dict:
    if not customer:
        frappe.throw(_('Customer is required'))
    c = frappe.get_doc('Customer', customer)
    phone = c.get('mobile_no') or c.get('phone')
    if not phone:
        frappe.throw(_('У клієнта не заповнений телефон'))
    return send_sms(phone=phone, text=text, sender=sender)
