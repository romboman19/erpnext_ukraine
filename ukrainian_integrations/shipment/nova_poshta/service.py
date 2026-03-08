from __future__ import annotations

import frappe
from frappe import _

from .api import NovaPoshtaClient


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def get_client() -> NovaPoshtaClient:
    api_key = _cfg('novaposhta_api_key')
    if not api_key:
        frappe.throw(_('Не задано novaposhta_api_key у site_config.json'))
    return NovaPoshtaClient(api_key)


@frappe.whitelist()
def track_ttn(ttn: str) -> dict:
    if not ttn:
        frappe.throw(_('TTN is required'))
    row = get_client().track(ttn)
    return {
        'ok': True,
        'ttn': ttn,
        'status': row.get('Status') or row.get('StatusCode') or '',
        'raw': row,
    }


@frappe.whitelist()
def sync_sales_invoice_ttn_statuses(limit: int = 50) -> dict:
    docs = frappe.get_all(
        'Sales Invoice',
        filters={'np_ttn_number': ['is', 'set']},
        fields=['name', 'np_ttn_number', 'np_status'],
        order_by='modified desc',
        limit=max(1, min(int(limit or 50), 500)),
    )
    if not docs:
        return {'ok': True, 'checked': 0, 'updated': 0}

    client = get_client()
    updated = 0
    for d in docs:
        ttn = d.get('np_ttn_number')
        if not ttn:
            continue
        try:
            row = client.track(ttn)
            status = row.get('Status') or row.get('StatusCode') or ''
            if status and status != (d.get('np_status') or ''):
                frappe.db.set_value('Sales Invoice', d['name'], 'np_status', status, update_modified=False)
                updated += 1
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Nova Poshta sync failed for {d['name']}")

    if updated:
        frappe.db.commit()
    return {'ok': True, 'checked': len(docs), 'updated': updated}


@frappe.whitelist()
def create_ttn_from_sales_invoice(
    sales_invoice: str,
    recipient_city_ref: str,
    recipient_warehouse_ref: str,
    weight: float = 1.0,
    seats_amount: int = 1,
    declared_cost: float | None = None,
) -> dict:
    if not sales_invoice:
        frappe.throw(_('Sales Invoice is required'))
    if not recipient_city_ref or not recipient_warehouse_ref:
        frappe.throw(_('Recipient city/warehouse refs are required'))

    si = frappe.get_doc('Sales Invoice', sales_invoice)
    sender_ref = _cfg('novaposhta_sender_ref')
    sender_city_ref = _cfg('novaposhta_sender_city_ref')
    sender_address_ref = _cfg('novaposhta_sender_address_ref')
    contact_sender_ref = _cfg('novaposhta_contact_sender_ref')
    sender_phone = _cfg('novaposhta_sender_phone')

    if not all([sender_ref, sender_city_ref, sender_address_ref, contact_sender_ref, sender_phone]):
        frappe.throw(_('Nova Poshta sender profile is incomplete in site_config'))

    cost = float(declared_cost) if declared_cost is not None else float(si.grand_total or 0)
    payload = {
        'PayerType': 'Recipient',
        'PaymentMethod': 'Cash',
        'DateTime': frappe.utils.nowdate(),
        'CargoType': 'Cargo',
        'Weight': float(weight or 1.0),
        'ServiceType': 'WarehouseWarehouse',
        'SeatsAmount': int(seats_amount or 1),
        'Description': f'Замовлення {si.name}',
        'Cost': max(1, int(round(cost))),
        'CitySender': sender_city_ref,
        'Sender': sender_ref,
        'SenderAddress': sender_address_ref,
        'ContactSender': contact_sender_ref,
        'SendersPhone': sender_phone,
        'CityRecipient': recipient_city_ref,
        'RecipientAddress': recipient_warehouse_ref,
        'ContactRecipient': si.customer_name or si.customer,
        'RecipientsPhone': (getattr(si, contact_mobile, None) or getattr(si, contact_phone, None) or getattr(si, contact_display, None) or '').strip(),
    }

    out = get_client().call('InternetDocument', 'save', payload)
    row = (out.get('data') or [{}])[0]
    ttn = row.get('IntDocNumber') or ''
    ttn_ref = row.get('Ref') or ''

    if 'np_ttn_number' in si.meta.get_valid_columns() and ttn:
        si.db_set('np_ttn_number', ttn, update_modified=False)
    if 'np_ttn_ref' in si.meta.get_valid_columns() and ttn_ref:
        si.db_set('np_ttn_ref', ttn_ref, update_modified=False)

    return {'ok': True, 'sales_invoice': si.name, 'ttn_number': ttn, 'ttn_ref': ttn_ref, 'raw': row}
