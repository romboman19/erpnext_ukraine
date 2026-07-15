from __future__ import annotations

import math

import frappe
import requests
from frappe import _

from ukrainian_integrations.payments.privat_pos.gateway_client import PrivatPOSGatewayClient
from ukrainian_integrations.utils.logger import log_event
from ukrainian_integrations.utils.operations import (
    canonical_hash,
    mark_operation,
    require_new_or_return_success,
    reserve_operation,
)
from ukrainian_integrations.utils.security import (
    ACCOUNTS_MANAGER_ROLES,
    ACCOUNTS_ROLES,
    SYSTEM_ROLES,
    permitted_doc,
    require_roles,
)


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _pb_pos_settings() -> dict:
    if frappe.db.exists('DocType', 'PB POS Settings'):
        d = frappe.get_single('PB POS Settings')
        return {
            'enabled': int(d.get('enabled') or 0),
            'gateway_url': (d.get('gateway_url') or '').strip(),
            'api_key': (d.get_password('api_key', raise_exception=False) or '').strip(),
            'timeout': int(d.get('request_timeout_sec') or 60),
            'protocol': (d.get('protocol') or 'legacy').strip(),
            'allow_test_operations': int(d.get('allow_test_operations') or 0),
        }
    return {
        'enabled': int(_cfg('pb_pos_enabled', 0) or 0),
        'gateway_url': (_cfg('pb_pos_gateway_url') or '').strip(),
        'api_key': (_cfg('pb_pos_api_key') or '').strip(),
        'timeout': int(_cfg('pb_pos_timeout', 60) or 60),
        'protocol': (_cfg('pb_pos_protocol', 'legacy') or 'legacy').strip(),
        'allow_test_operations': int(_cfg('pb_pos_allow_test_operations', 0) or 0),
    }


def _resolve_terminal(terminal: str) -> dict:
    if not terminal:
        frappe.throw(_('Terminal is required'))
    if not frappe.db.exists('DocType', 'PB POS Terminal'):
        frappe.throw(_('DocType PB POS Terminal not found'))

    name = terminal
    if not frappe.db.exists('PB POS Terminal', name):
        name = (
            frappe.db.get_value('PB POS Terminal', {'terminal_name': terminal}, 'name')
            or frappe.db.get_value('PB POS Terminal', {'ip_address': terminal, 'is_active': 1}, 'name')
        )
        if not name:
            frappe.throw(_('PB POS Terminal not found: {0}').format(terminal))

    d = frappe.get_doc('PB POS Terminal', name)
    if int(d.get('is_active') or 0) != 1:
        frappe.throw(_('Terminal is inactive'))

    ip = (d.get('ip_address') or '').strip()
    if not ip:
        frappe.throw(_('Terminal IP is empty'))

    return {
        'name': d.name,
        'terminal_name': d.get('terminal_name') or d.name,
        'ip': ip,
        'port': int(d.get('tcp_port') or 2000),
    }


def _client() -> PrivatPOSGatewayClient:
    cfg = _pb_pos_settings()
    base_url = cfg.get('gateway_url')
    api_key = cfg.get('api_key')
    timeout = cfg.get('timeout', 20)
    protocol = cfg.get('protocol', 'legacy')
    if int(cfg.get('enabled') or 0) != 1:
        frappe.throw(_('PB POS integration is disabled'))
    if not base_url:
        frappe.throw(_('Не задано pb_pos_gateway_url (site_config або PB POS Settings.gateway_url)'))
    if not api_key:
        frappe.throw(_('Не задано pb_pos_api_key (site_config або PB POS Settings.api_key)'))
    return PrivatPOSGatewayClient(base_url=base_url, api_key=api_key, timeout=timeout, protocol=protocol)



class GatewayRejectedError(frappe.ValidationError):
    pass


class GatewayAmbiguousError(RuntimeError):
    pass


def _assert_gateway_ok(response: dict, action: str):
    if not isinstance(response, dict):
        raise GatewayAmbiguousError(_('{0} failed: invalid gateway response').format(action))

    desc = str(response.get('description') or response.get('errorDescription') or response.get('message') or '')
    rc = str(response.get('responseCode') or '').strip()

    # Legacy Verify may return 0001 with empty receipt log, but transport is OK (terminal reachable)
    if action == 'Connection test' and ('log file is empty' in desc.lower() or rc == '0001'):
        return

    if response.get('ok') is False or bool(response.get('error')):
        raise GatewayRejectedError(_('{0} failed: {1}').format(action, desc or _('Unknown gateway error')))

    if rc and rc != '0000':
        raise GatewayRejectedError(_('{0} failed: {1}').format(action, desc or _('Gateway response code: {0}').format(rc)))

    if not response:
        raise GatewayAmbiguousError(_('{0} failed: empty gateway response').format(action))

    status = str(response.get('status') or response.get('result') or '').strip().lower()
    explicitly_approved = (
        rc == '0000'
        or response.get('ok') is True
        or response.get('success') is True
        or status in {'approved', 'ok', 'success', 'succeeded'}
        or bool(_call_external_id(response))
    )
    if action not in {'Connection test', 'Healthcheck'} and not explicitly_approved:
        raise GatewayAmbiguousError(_('{0} failed: gateway did not provide explicit approval').format(action))


def _lock_terminal(name: str) -> None:
    frappe.db.sql('select name from `tabPB POS Terminal` where name=%s for update', (name,))


def _call_external_id(response: dict) -> str | None:
    value = (
        response.get('invoice_number')
        or response.get('invoiceNumber')
        or response.get('rrn')
        or response.get('operation_id')
    )
    return str(value) if value else None


def _mark_transport_failure(operation, exc: requests.RequestException) -> None:
    status_code = getattr(getattr(exc, 'response', None), 'status_code', None)
    if status_code is not None and 400 <= int(status_code) < 500:
        mark_operation(operation, 'failed', error=f'PB POS gateway HTTP {status_code}')
    else:
        mark_operation(operation, 'unknown', error=frappe.get_traceback())


@frappe.whitelist()
def pb_pos_healthcheck() -> dict:
    require_roles(*SYSTEM_ROLES)
    try:
        out = _client().ping()
        _assert_gateway_ok(out, 'Healthcheck')
        log_event('privat_pos', 'success', 'Healthcheck OK', response_payload=out)
        return {'ok': True, 'response': out}
    except Exception:
        log_event('privat_pos', 'error', 'Healthcheck failed', error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def pb_pos_sale(
    sales_invoice: str,
    terminal_ip: str,
    amount: float | None = None,
    terminal_port: int = 2000,
    idempotency_key: str | None = None,
) -> dict:
    require_roles(*ACCOUNTS_ROLES)
    if not sales_invoice:
        frappe.throw(_('Sales Invoice is required'))
    if not terminal_ip:
        frappe.throw(_('Terminal IP is required'))
    if not (idempotency_key or '').strip():
        frappe.throw(_('idempotency_key is required'))

    si = permitted_doc('Sales Invoice', sales_invoice, 'read')
    if int(si.docstatus or 0) != 1:
        frappe.throw(_('Sales Invoice must be submitted'))
    if str(si.currency or '').upper() != 'UAH':
        frappe.throw(_('PB POS currently supports only UAH Sales Invoices'))
    terminal = _resolve_terminal(terminal_ip)
    outstanding = float(si.outstanding_amount or 0)
    if not math.isfinite(outstanding) or outstanding <= 0:
        frappe.throw(_('Sales Invoice has no positive outstanding amount'))
    sale_amount = float(amount) if amount is not None else outstanding
    if not math.isfinite(sale_amount) or sale_amount <= 0:
        frappe.throw(_('Сума оплати має бути більшою за 0'))
    if sale_amount > outstanding + 0.005:
        frappe.throw(_('Payment amount exceeds invoice outstanding amount'))

    operation_id = (idempotency_key or '').strip()
    payload = {
        'sales_invoice': si.name,
        'terminal': terminal['name'],
        'terminal_ip': terminal['ip'],
        'terminal_port': terminal['port'],
        'amount': sale_amount,
        'operation_id': operation_id,
    }

    log_event('privat_pos', 'queued', f'Sale start for {si.name}', reference_doctype='Sales Invoice', reference_name=si.name, request_payload=payload)

    reservation = reserve_operation(
        idempotency_key=f'privat_pos:sale:{operation_id}',
        integration='privat_pos',
        operation_type='sale',
        request_payload=payload,
        reference_doctype='Sales Invoice',
        reference_name=si.name,
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return cached
    client = _client()
    mark_operation(reservation.doc, 'unknown', response_payload={'phase': 'external_request_in_progress'})
    _lock_terminal(terminal['name'])

    try:
        res = client.sale(
            terminal_ip=terminal['ip'],
            port=terminal['port'],
            amount=sale_amount,
            operation_id=operation_id,
        )
        _assert_gateway_ok(res, 'Sale')

        for field, value in {
            'pb_pos_status': res.get('status') or res.get('result') or 'success',
            'pb_pos_rrn': res.get('rrn') or '',
            'pb_pos_invoice_number': res.get('invoice_number') or res.get('invoiceNumber') or '',
            'pb_pos_card_mask': res.get('card_mask') or res.get('cardMask') or '',
        }.items():
            if field in si.meta.get_valid_columns() and value:
                si.db_set(field, value, update_modified=False)

        result = {'ok': True, 'sales_invoice': si.name, 'operation_id': operation_id, 'response': res}
        mark_operation(reservation.doc, 'succeeded', external_id=res.get('invoice_number') or res.get('invoiceNumber') or res.get('rrn'), response_payload=result)
        log_event('privat_pos', 'success', f'Sale done for {si.name}', reference_doctype='Sales Invoice', reference_name=si.name, request_payload=payload, response_payload=res)
        return result
    except requests.RequestException as exc:
        _mark_transport_failure(reservation.doc, exc)
        log_event('privat_pos', 'error', f'Sale outcome unknown for {si.name}; reconciliation required', reference_doctype='Sales Invoice', reference_name=si.name, request_payload=payload, error_trace=frappe.get_traceback())
        raise
    except GatewayRejectedError:
        mark_operation(reservation.doc, 'failed', error=frappe.get_traceback())
        log_event('privat_pos', 'error', f'Sale failed for {si.name}', reference_doctype='Sales Invoice', reference_name=si.name, request_payload=payload, error_trace=frappe.get_traceback())
        raise
    except Exception:
        mark_operation(reservation.doc, 'unknown', error=frappe.get_traceback())
        log_event('privat_pos', 'error', f'Sale outcome unknown for {si.name}; reconciliation required', reference_doctype='Sales Invoice', reference_name=si.name, request_payload=payload, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def pb_pos_test_connection(terminal: str) -> dict:
    require_roles(*ACCOUNTS_MANAGER_ROLES)
    t = _resolve_terminal(terminal)
    out = _client().ping(terminal_ip=t['ip'])
    _assert_gateway_ok(out, 'Connection test')
    log_event('privat_pos', 'success', f"Terminal connection test OK {t['name']}", request_payload=t, response_payload=out)
    return {'ok': True, 'terminal': t, 'gateway': out}


@frappe.whitelist()
def pb_pos_test_payment(terminal: str, idempotency_key: str, amount: float = 1.0) -> dict:
    require_roles(*SYSTEM_ROLES)
    if int(_pb_pos_settings().get('allow_test_operations') or 0) != 1:
        frappe.throw(_('Real PB POS test operations are disabled'))
    t = _resolve_terminal(terminal)
    amt = float(amount or 0)
    if not math.isfinite(amt) or amt <= 0:
        frappe.throw(_('Amount must be > 0'))
    max_test_amount = max(1.0, min(float(frappe.conf.get('pb_pos_max_test_amount', 100) or 100), 10_000.0))
    if amt > max_test_amount:
        frappe.throw(_('Test amount exceeds the configured maximum ({0})').format(max_test_amount))
    if not (idempotency_key or '').strip():
        frappe.throw(_('idempotency_key is required'))

    operation_id = f"TEST-SALE-{canonical_hash({'idempotency_key': idempotency_key})[:12]}"
    req = {'terminal': t, 'amount': amt, 'operation_id': operation_id}
    reservation = reserve_operation(
        idempotency_key=f'privat_pos:test_sale:{idempotency_key}',
        integration='privat_pos',
        operation_type='test_sale',
        request_payload=req,
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return cached
    client = _client()
    log_event('privat_pos', 'queued', f"Test sale start {t['name']}", request_payload=req)
    mark_operation(reservation.doc, 'unknown', response_payload={'phase': 'external_request_in_progress'})
    _lock_terminal(t['name'])
    try:
        res = client.sale(terminal_ip=t['ip'], port=t['port'], amount=amt, operation_id=operation_id)
        _assert_gateway_ok(res, 'Test payment')
    except GatewayRejectedError:
        mark_operation(reservation.doc, 'failed', error=frappe.get_traceback())
        raise
    except Exception:
        mark_operation(reservation.doc, 'unknown', error=frappe.get_traceback())
        raise
    result = {'ok': True, 'terminal': t, 'response': res, 'operation_id': operation_id}
    mark_operation(reservation.doc, 'succeeded', external_id=_call_external_id(res), response_payload=result)
    log_event('privat_pos', 'success', f"Test sale done {t['name']}", request_payload=req, response_payload=res)
    return result


@frappe.whitelist()
def pb_pos_test_refund(
    terminal: str,
    idempotency_key: str,
    amount: float = 1.0,
    reference_operation_id: str | None = None,
) -> dict:
    require_roles(*SYSTEM_ROLES)
    if int(_pb_pos_settings().get('allow_test_operations') or 0) != 1:
        frappe.throw(_('Real PB POS test operations are disabled'))
    t = _resolve_terminal(terminal)
    amt = float(amount or 0)
    if not math.isfinite(amt) or amt <= 0:
        frappe.throw(_('Amount must be > 0'))
    max_test_amount = max(1.0, min(float(frappe.conf.get('pb_pos_max_test_amount', 100) or 100), 10_000.0))
    if amt > max_test_amount:
        frappe.throw(_('Test amount exceeds the configured maximum ({0})').format(max_test_amount))
    if not reference_operation_id:
        frappe.throw(_('Для тестового повернення вкажіть Reference Operation ID (invoiceNumber з чеку)'))
    if not (idempotency_key or '').strip():
        frappe.throw(_('idempotency_key is required'))

    operation_id = f"TEST-REFUND-{canonical_hash({'idempotency_key': idempotency_key})[:12]}"
    req = {
        'terminal': t,
        'amount': amt,
        'operation_id': operation_id,
        'reference_operation_id': reference_operation_id,
    }
    reservation = reserve_operation(
        idempotency_key=f'privat_pos:test_refund:{idempotency_key}',
        integration='privat_pos',
        operation_type='test_refund',
        request_payload=req,
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return cached
    client = _client()
    log_event('privat_pos', 'queued', f"Test refund start {t['name']}", request_payload=req)
    mark_operation(reservation.doc, 'unknown', response_payload={'phase': 'external_request_in_progress'})
    _lock_terminal(t['name'])
    try:
        res = client.refund(
            terminal_ip=t['ip'],
            port=t['port'],
            amount=amt,
            operation_id=operation_id,
            reference_operation_id=reference_operation_id,
        )
        _assert_gateway_ok(res, 'Test refund')
    except GatewayRejectedError:
        mark_operation(reservation.doc, 'failed', error=frappe.get_traceback())
        raise
    except Exception:
        mark_operation(reservation.doc, 'unknown', error=frappe.get_traceback())
        raise
    result = {'ok': True, 'terminal': t, 'response': res, 'operation_id': operation_id}
    mark_operation(reservation.doc, 'succeeded', external_id=_call_external_id(res), response_payload=result)
    log_event('privat_pos', 'success', f"Test refund done {t['name']}", request_payload=req, response_payload=res)
    return result
