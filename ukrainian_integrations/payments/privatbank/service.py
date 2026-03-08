from __future__ import annotations

from datetime import date, timedelta

import frappe
from frappe import _

from ukrainian_integrations.payments.privatbank.client import PrivatbankClient
from ukrainian_integrations.utils.logger import log_event


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _client() -> PrivatbankClient:
    token = _cfg('privatbank_token')
    base_url = _cfg('privatbank_api_base', 'https://acp.privatbank.ua/api/proxy')
    if not token:
        frappe.throw(_('Не задано privatbank_token у site_config.json'))
    return PrivatbankClient(token=token, base_url=base_url)


def _default_range(days: int = 1) -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=max(0, int(days)))
    return start.isoformat(), end.isoformat()


@frappe.whitelist()
def pb_statements_fetch(account: str | None = None, start_date: str | None = None, end_date: str | None = None, limit: int = 1000, offset: int = 0) -> dict:
    acc = (account or _cfg('privatbank_account') or '').strip()
    if not acc:
        frappe.throw(_('Не задано рахунок: передай account або privatbank_account у site_config'))

    if not start_date or not end_date:
        start_date, end_date = _default_range(days=1)

    request_payload = {
        'account': acc,
        'startDate': start_date,
        'endDate': end_date,
        'limit': int(limit),
        'offset': int(offset),
    }

    log_event('privatbank', 'queued', 'Fetch statements', request_payload=request_payload)
    try:
        out = _client().statements(
            account=acc,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        rows = out.get('list') or out.get('transactions') or []
        log_event('privatbank', 'success', f'Statements fetched: {len(rows)}', request_payload=request_payload, response_payload={'count': len(rows)})
        return {'ok': True, 'count': len(rows), 'data': out}
    except Exception:
        log_event('privatbank', 'error', 'Fetch statements failed', request_payload=request_payload, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def pb_statements_import_to_bank_transactions(account: str | None = None, start_date: str | None = None, end_date: str | None = None, company: str | None = None) -> dict:
    fetched = pb_statements_fetch(account=account, start_date=start_date, end_date=end_date)
    raw = fetched.get('data') or {}
    rows = raw.get('list') or raw.get('transactions') or []

    created = 0
    skipped = 0
    comp = company or _cfg('default_company')

    for row in rows:
        tx_id = str(row.get('id') or row.get('transactionId') or row.get('ref') or '').strip()
        if not tx_id:
            skipped += 1
            continue

        exists = frappe.db.exists(
            'Bank Transaction',
            {'description': ['like', f'%PBX:{tx_id}%']},
        )
        if exists:
            skipped += 1
            continue

        amount = row.get('amount') or row.get('sum') or 0
        try:
            amount = float(amount) / 100 if abs(float(amount)) > 1000 else float(amount)
        except Exception:
            amount = 0

        posting_date = row.get('date') or row.get('operationDate') or frappe.utils.nowdate()
        if isinstance(posting_date, str) and 'T' in posting_date:
            posting_date = posting_date.split('T', 1)[0]

        description = row.get('description') or row.get('purpose') or ''

        doc = frappe.get_doc(
            {
                'doctype': 'Bank Transaction',
                'date': posting_date,
                'deposit': amount if amount > 0 else 0,
                'withdrawal': abs(amount) if amount < 0 else 0,
                'currency': row.get('ccy') or 'UAH',
                'description': f'PBX:{tx_id} | {description}',
                'bank_account_no': (account or _cfg('privatbank_account') or ''),
                'company': comp,
            }
        )
        doc.insert(ignore_permissions=True)
        created += 1

    if created:
        frappe.db.commit()

    log_event(
        'privatbank',
        'success',
        f'Imported statements to Bank Transaction: created={created}, skipped={skipped}',
        request_payload={'account': account, 'start_date': start_date, 'end_date': end_date},
        response_payload={'created': created, 'skipped': skipped},
    )
    return {'ok': True, 'created': created, 'skipped': skipped}
