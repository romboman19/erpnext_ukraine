from __future__ import annotations

import frappe
from frappe import _

from ukrainian_integrations.shipment.ukr_poshta.api import UkrPoshtaClient
from ukrainian_integrations.utils.logger import log_event


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def get_client() -> UkrPoshtaClient:
    ecom = _cfg('ukrposhta_ecom_token')
    tracking = _cfg('ukrposhta_tracking_token')
    api_base = _cfg('ukrposhta_api_base', 'https://www.ukrposhta.ua/ecom/0.0.1')
    if not ecom:
        frappe.throw(_('Не задано ukrposhta_ecom_token у site_config.json'))
    return UkrPoshtaClient(ecom_token=ecom, tracking_token=tracking, api_base=api_base)


@frappe.whitelist()
def track_barcode(barcode: str) -> dict:
    if not barcode:
        frappe.throw(_('Barcode is required'))
    try:
        row = get_client().track(barcode)
        log_event('ukr_poshta', 'success', f'Track {barcode}', request_payload={'barcode': barcode}, response_payload=row)
        return {'ok': True, 'barcode': barcode, 'raw': row}
    except Exception:
        log_event('ukr_poshta', 'error', f'Track failed {barcode}', request_payload={'barcode': barcode}, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def sync_sales_invoice_up_statuses(limit: int = 50) -> dict:
    docs = frappe.get_all(
        'Sales Invoice',
        filters={'up_barcode': ['is', 'set']},
        fields=['name', 'up_barcode', 'up_status'],
        order_by='modified desc',
        limit=max(1, min(int(limit or 50), 500)),
    )
    if not docs:
        return {'ok': True, 'checked': 0, 'updated': 0}

    client = get_client()
    updated = 0
    for d in docs:
        code = d.get('up_barcode')
        if not code:
            continue
        try:
            row = client.track(code)
            status = row.get('status') or row.get('eventName') or row.get('state') or ''
            if status and status != (d.get('up_status') or ''):
                frappe.db.set_value('Sales Invoice', d['name'], 'up_status', status, update_modified=False)
                updated += 1
        except Exception:
            log_event(
                'ukr_poshta',
                'error',
                f"Sync failed for {d['name']}",
                reference_doctype='Sales Invoice',
                reference_name=d['name'],
                request_payload={'barcode': code},
                error_trace=frappe.get_traceback(),
            )

    if updated:
        frappe.db.commit()
    return {'ok': True, 'checked': len(docs), 'updated': updated}


@frappe.whitelist()
def create_shipment_from_sales_invoice(sales_invoice: str, recipient: dict | None = None, parcel: dict | None = None) -> dict:
    if not sales_invoice:
        frappe.throw(_('Sales Invoice is required'))

    si = frappe.get_doc('Sales Invoice', sales_invoice)
    recipient = recipient or {}
    parcel = parcel or {}

    sender = {
        'name': _cfg('ukrposhta_sender_name', 'HUNTER'),
        'phoneNumber': _cfg('ukrposhta_sender_phone', ''),
        'address': {
            'postcode': _cfg('ukrposhta_sender_postcode', ''),
            'country': 'UA',
            'region': _cfg('ukrposhta_sender_region', ''),
            'city': _cfg('ukrposhta_sender_city', ''),
            'street': _cfg('ukrposhta_sender_street', ''),
            'houseNumber': _cfg('ukrposhta_sender_house', ''),
        },
    }

    recv = {
        'name': recipient.get('name') or si.customer_name or si.customer,
        'phoneNumber': recipient.get('phone') or getattr(si, 'contact_mobile', None) or getattr(si, 'contact_phone', None) or getattr(si, 'contact_display', None) or '',
        'address': {
            'postcode': recipient.get('postcode') or '',
            'country': 'UA',
            'region': recipient.get('region') or '',
            'city': recipient.get('city') or '',
            'street': recipient.get('street') or '',
            'houseNumber': recipient.get('house') or '',
            'apartmentNumber': recipient.get('apartment') or '',
        },
    }

    payload = {
        'sender': sender,
        'recipient': recv,
        'deliveryType': parcel.get('deliveryType') or 'W2W',
        'weight': float(parcel.get('weight') or 1.0),
        'declaredPrice': float(parcel.get('declaredPrice') or si.grand_total or 1),
        'description': parcel.get('description') or f'Замовлення {si.name}',
    }

    out = get_client().request('shipments', method='POST', payload=payload, token_kind='ecom')
    barcode = out.get('barcode') or out.get('barcodeNumber') or ''
    if 'up_barcode' in si.meta.get_valid_columns() and barcode:
        si.db_set('up_barcode', barcode, update_modified=False)
    return {'ok': True, 'sales_invoice': si.name, 'barcode': barcode, 'raw': out}
