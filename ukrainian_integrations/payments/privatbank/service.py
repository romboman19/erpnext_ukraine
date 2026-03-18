from __future__ import annotations

from datetime import date, timedelta

import frappe
from frappe import _

from ukrainian_integrations.payments.privatbank.client import PrivatbankClient
from ukrainian_integrations.utils.logger import log_event


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _pb_profiles() -> list[dict]:
    if not frappe.db.exists("DocType", "PrivatBank Settings") or not frappe.db.exists("DocType", "PrivatBank Profile"):
        return []
    try:
        d = frappe.get_single("PrivatBank Settings")
        rows = d.get("profiles") or []
        out = []
        for r in rows:
            token = ""
            if hasattr(r, "get_password"):
                try:
                    token = (r.get_password("token") or "").strip()
                except Exception:
                    token = ""
            out.append({
                "name": r.get("name"),
                "label": (r.get("label") or "").strip(),
                "enabled": int(r.get("enabled") or 0),
                "is_default": int(r.get("is_default") or 0),
                "token": token,
                "api_base": (r.get("api_base") or "").strip(),
                "group_client_id": (r.get("group_client_id") or "").strip(),
                "account": (r.get("account") or "").strip(),
                "bank_account": (r.get("bank_account") or "").strip(),
                "company": (r.get("company") or "").strip(),
                "amount_in_minor_units": (int(r.get("amount_in_minor_units")) if (r.get("amount_in_minor_units") is not None and str(r.get("amount_in_minor_units")) != "") else 1),
                "auto_import_enabled": int(r.get("auto_import_enabled") or 0),
                "auto_import_days_back": int(r.get("auto_import_days_back") or 1),
            })
        return out
    except Exception:
        return []


def _pick_profile(profile: str | None = None) -> dict:
    profs = _pb_profiles()
    if not profs:
        return {}
    if profile:
        p = next((x for x in profs if x.get("name") == profile or x.get("label") == profile), None)
        if p:
            return p
    p = next((x for x in profs if x.get("is_default") == 1 and x.get("enabled") == 1), None)
    if p:
        return p
    p = next((x for x in profs if x.get("enabled") == 1), None)
    return p or {}


def _pb_settings() -> dict:
    if not frappe.db.exists("DocType", "PrivatBank Settings"):
        return {}
    try:
        d = frappe.get_single("PrivatBank Settings")
        return {"enabled": int(d.get("enabled") or 0)}
    except Exception:
        return {}


def _client(token: str | None = None, base_url: str | None = None) -> PrivatbankClient:
    token = (token or _cfg('privatbank_token') or "").strip()
    base_url = (base_url or _cfg('privatbank_api_base', 'https://acp.privatbank.ua/api/proxy') or "").strip()
    if not token:
        frappe.throw(_('Не задано privatbank_token у site_config.json'))
    return PrivatbankClient(token=token, base_url=base_url)


def _default_range(days: int = 1) -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=max(0, int(days)))
    return start.isoformat(), end.isoformat()


def _to_pb_date(d: str) -> str:
    # accepts YYYY-MM-DD and returns DD-MM-YYYY
    if not d:
        return d
    if "-" in d and len(d) == 10:
        y, m, day = d.split("-")
        if len(y) == 4:
            return f"{day}-{m}-{y}"
    return d


def _normalize_amount(raw_amount, in_minor_units: int = 1) -> float:
    """Normalize PrivatBank statement amount to major currency units.

    Controlled by `privatbank_amount_in_minor_units` (site_config, default=1).
    If 1, divide by 100 (kopecks -> UAH).
    If 0, keep as-is (already UAH).
    """
    try:
        value = float(raw_amount or 0)
    except Exception:
        return 0.0
    in_minor = int(in_minor_units if in_minor_units is not None else (_cfg("privatbank_amount_in_minor_units", 1) or 1)) == 1
    return value / 100.0 if in_minor else value


@frappe.whitelist()
def pb_statements_fetch(account: str | None = None, start_date: str | None = None, end_date: str | None = None, limit: int = 100, follow_id: str | None = None, profile: str | None = None) -> dict:
    prof = _pick_profile(profile)
    acc = (account or prof.get('account') or _cfg('privatbank_account') or '').strip()
    if not acc:
        frappe.throw(_('Не задано рахунок: передай account або privatbank_account у site_config'))

    if not start_date or not end_date:
        start_date, end_date = _default_range(days=1)

    request_payload = {
        'account': acc,
        'startDate': start_date,
        'endDate': end_date,
        'limit': int(limit),
        'follow_id': follow_id,
    }

    log_event('privatbank', 'queued', 'Fetch statements', request_payload=request_payload)
    try:
        out = _client(token=(prof.get("token") or None), base_url=(prof.get("api_base") or None)).statements(
            account=acc,
            start_date=_to_pb_date(start_date),
            end_date=_to_pb_date(end_date),
            limit=limit,
            follow_id=follow_id,
            group_id=(prof.get("group_client_id") or None),
        )
        rows = out.get('list') or out.get('transactions') or []
        log_event('privatbank', 'success', f'Statements fetched: {len(rows)}', request_payload=request_payload, response_payload={'count': len(rows)})
        return {'ok': True, 'count': len(rows), 'data': out}
    except Exception:
        log_event('privatbank', 'error', 'Fetch statements failed', request_payload=request_payload, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def pb_statements_import_to_bank_transactions(account: str | None = None, start_date: str | None = None, end_date: str | None = None, company: str | None = None, profile: str | None = None) -> dict:
    prof = _pick_profile(profile)
    fetched = pb_statements_fetch(account=account, start_date=start_date, end_date=end_date, profile=profile)
    raw = fetched.get('data') or {}
    rows = raw.get('list') or raw.get('transactions') or []

    created = 0
    skipped = 0
    comp = company or prof.get('company') or _cfg('default_company')

    for row in rows:
        tx_id = str(row.get('id') or row.get('ID') or row.get('transactionId') or row.get('TECHNICAL_TRANSACTION_ID') or row.get('ref') or row.get('REF') or '').strip()
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

        amount = _normalize_amount(row.get('amount') or row.get('sum') or row.get('SUM') or row.get('SUM_E') or 0, in_minor_units=prof.get('amount_in_minor_units', 1))

        trantype = str(row.get('TRANTYPE') or row.get('trantype') or '').upper()
        if trantype == 'D' and amount > 0:
            amount = -amount
        elif trantype == 'C' and amount < 0:
            amount = abs(amount)

        posting_date = row.get('date') or row.get('operationDate') or row.get('DAT_OD') or row.get('DATE_TIME_DAT_OD_TIM_P') or frappe.utils.nowdate()
        if isinstance(posting_date, str) and 'T' in posting_date:
            posting_date = posting_date.split('T', 1)[0]
        if isinstance(posting_date, str) and ' ' in posting_date and '.' in posting_date:
            posting_date = posting_date.split(' ',1)[0]
        if isinstance(posting_date, str) and '.' in posting_date and len(posting_date) >= 10:
            d,m,y = posting_date[:10].split('.')
            posting_date = f"{y}-{m}-{d}"

        description = row.get('description') or row.get('purpose') or row.get('OSND') or ''

        doc = frappe.get_doc(
            {
                'doctype': 'Bank Transaction',
                'date': posting_date,
                'deposit': amount if amount > 0 else 0,
                'withdrawal': abs(amount) if amount < 0 else 0,
                'currency': row.get('ccy') or row.get('CCY') or 'UAH',
                'description': f'PBX:{tx_id} | {description}',
                'bank_account': (prof.get('bank_account') or ''),
                'bank_account_no': (account or prof.get('account') or _cfg('privatbank_account') or ''),
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


@frappe.whitelist()
def pb_list_accounts(profile: str | None = None) -> dict:
    prof = _pick_profile(profile)
    if not prof:
        frappe.throw(_("PrivatBank profile not found"))

    # According to docs, /settings may not include account list. Discover accounts via /balance without acc.
    today = date.today().strftime("%d-%m-%Y")
    out = _client(token=(prof.get("token") or None), base_url=(prof.get("api_base") or None)).balances(
        account=None,
        start_date=today,
        end_date=today,
        limit=100,
        group_id=(prof.get("group_client_id") or None),
    )
    data = out.get("balances") or out.get("data") or []
    norm = []
    for a in data:
        acc = a.get("acc") or a.get("account") or ""
        ccy = a.get("currency") or a.get("ccy") or ""
        if acc:
            norm.append({"account": acc, "currency": ccy, "raw": a, "label": f"{acc} | {ccy}"})
    # de-dup by account
    uniq = {}
    for x in norm:
        uniq[x["account"]] = x
    norm = list(uniq.values())
    return {"ok": True, "count": len(norm), "accounts": norm, "raw": out}


@frappe.whitelist()
def pb_bind_account(account: str, bank_account: str | None = None, profile: str | None = None) -> dict:
    if not account:
        frappe.throw(_("account is required"))
    d = frappe.get_single("PrivatBank Settings")
    if not profile:
        frappe.throw(_("profile is required"))
    row = next((r for r in (d.get("profiles") or []) if r.get("name") == profile or r.get("label") == profile), None)
    if not row:
        frappe.throw(_("PrivatBank profile not found"))
    row.account = (account or "").strip()
    if bank_account is not None:
        row.bank_account = (bank_account or "").strip()
    d.save(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True, "profile": profile, "account": row.account, "bank_account": row.get("bank_account")}
