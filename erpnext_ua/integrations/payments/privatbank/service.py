from __future__ import annotations

import math
from datetime import date, timedelta

import frappe
from frappe import _

from erpnext_ua.integrations.payments.privatbank.client import PrivatbankClient
from erpnext_ua.integrations.utils.logger import log_event
from erpnext_ua.integrations.utils.operations import canonical_hash
from erpnext_ua.integrations.utils.security import ACCOUNTS_MANAGER_ROLES, require_roles
from erpnext_ua.integrations.utils.validation import validate_erp_bank_account


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _pb_profiles() -> list[dict]:
    if not frappe.db.exists("DocType", "PrivatBank Settings") or not frappe.db.exists("DocType", "PrivatBank Profile"):
        return []
    d = frappe.get_single("PrivatBank Settings")
    rows = d.get("profiles") or []
    out = []
    for r in rows:
        credential = ""
        if hasattr(r, "get_password"):
            credential = (r.get_password("token", raise_exception=False) or "").strip()
        out.append({
                "name": r.get("name"),
                "label": (r.get("label") or "").strip(),
                "enabled": int(r.get("enabled") or 0),
                "is_default": int(r.get("is_default") or 0),
                "token": credential or "",
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


def _pick_profile(profile: str | None = None) -> dict:
    profs = _pb_profiles()
    if not profs:
        return {}
    if profile:
        p = next((x for x in profs if (x.get("name") == profile or x.get("label") == profile) and x.get("enabled") == 1), None)
        return p or {}
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
    selected_token = _cfg('privatbank_token') if token is None else token
    selected_base_url = _cfg('privatbank_api_base', 'https://acp.privatbank.ua/api/proxy') if base_url is None else base_url
    selected_token = (selected_token or "").strip()
    selected_base_url = (selected_base_url or "").strip()
    if not selected_token:
        frappe.throw(_('Не задано privatbank_token у site_config.json'))
    return PrivatbankClient(token=selected_token, base_url=selected_base_url)


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
        raise ValueError("PrivatBank amount is not numeric") from None
    if not math.isfinite(value):
        raise ValueError("PrivatBank amount is not finite")
    in_minor = int(in_minor_units if in_minor_units is not None else (_cfg("privatbank_amount_in_minor_units", 1) or 1)) == 1
    return value / 100.0 if in_minor else value


def _first_present(row: dict, *keys: str, default=None):
    for key in keys:
        if key in row and row.get(key) is not None:
            return row.get(key)
    return default


def _currency_code(value, default: str) -> str:
    code = str(value or default).strip().upper()
    return {"980": "UAH", "840": "USD", "978": "EUR"}.get(code, code)


def _pagination_state(payload: dict) -> tuple[bool, str | None]:
    raw_has_next = _first_present(payload, 'exist_next_page', 'existNextPage', 'has_next_page', 'hasNextPage', default=False)
    has_next = str(raw_has_next).strip().lower() in {'1', 'true', 'yes'} if isinstance(raw_has_next, str) else bool(raw_has_next)
    next_id = (
        payload.get('next_page_id')
        or payload.get('nextPageId')
        or payload.get('followId')
        or payload.get('next_follow_id')
    )
    return has_next, str(next_id).strip() if next_id else None


@frappe.whitelist()
def pb_statements_fetch(account: str | None = None, start_date: str | None = None, end_date: str | None = None, limit: int = 100, follow_id: str | None = None, profile: str | None = None, max_pages: int = 100) -> dict:
    require_roles(*ACCOUNTS_MANAGER_ROLES)
    prof = _pick_profile(profile)
    if _pb_profiles() and not prof:
        frappe.throw(_("Enabled PrivatBank profile not found"))
    if frappe.db.exists("DocType", "PrivatBank Settings") and int(_pb_settings().get("enabled") or 0) != 1:
        frappe.throw(_("PrivatBank integration is disabled"))
    acc = (account or prof.get('account') or _cfg('privatbank_account') or '').strip()
    if not acc:
        frappe.throw(_('Не задано рахунок: передай account або privatbank_account у site_config'))

    if not start_date or not end_date:
        start_date, end_date = _default_range(days=1)
    start = frappe.utils.getdate(start_date)
    end = frappe.utils.getdate(end_date)
    if start > end or (end - start).days > 366:
        frappe.throw(_("PrivatBank statement range must be ordered and no longer than 366 days"))

    request_payload = {
        'account': acc,
        'startDate': start_date,
        'endDate': end_date,
        'limit': int(limit),
        'follow_id': follow_id,
    }

    log_event('privatbank', 'queued', 'Fetch statements', request_payload=request_payload)
    try:
        client = _client(
            token=(prof.get("token") if prof else None),
            base_url=(prof.get("api_base") if prof else None),
        )
        all_rows = []
        page_count = 0
        cursor = follow_id
        seen_cursors = set()
        last_payload = {}
        while page_count < max(1, min(int(max_pages or 100), 500)):
            out = client.statements(
                account=acc,
                start_date=_to_pb_date(start_date),
                end_date=_to_pb_date(end_date),
                limit=max(1, min(int(limit or 100), 1000)),
                follow_id=cursor,
                group_id=(prof.get("group_client_id") or None),
            )
            last_payload = out
            rows = out.get('list') or out.get('transactions') or []
            if not isinstance(rows, list):
                raise ValueError('PrivatBank statements payload must contain a list')
            all_rows.extend(rows)
            page_count += 1
            has_next, next_cursor = _pagination_state(out)
            if not has_next:
                break
            if not next_cursor or next_cursor in seen_cursors:
                raise RuntimeError('PrivatBank pagination cursor is missing or repeated')
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise RuntimeError('PrivatBank pagination exceeded max_pages')

        merged = dict(last_payload)
        merged['list'] = all_rows
        merged['transactions'] = all_rows
        merged['pages_fetched'] = page_count
        log_event('privatbank', 'success', f'Statements fetched: {len(all_rows)}', request_payload=request_payload, response_payload={'count': len(all_rows), 'pages': page_count})
        return {'ok': True, 'count': len(all_rows), 'pages': page_count, 'data': merged}
    except Exception:
        log_event('privatbank', 'error', 'Fetch statements failed', request_payload=request_payload, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def pb_statements_import_to_bank_transactions(account: str | None = None, start_date: str | None = None, end_date: str | None = None, company: str | None = None, profile: str | None = None) -> dict:
    require_roles(*ACCOUNTS_MANAGER_ROLES)
    prof = _pick_profile(profile)
    fetched = pb_statements_fetch(account=account, start_date=start_date, end_date=end_date, profile=profile)
    raw = fetched.get('data') or {}
    rows = raw.get('list') or raw.get('transactions') or []

    created = 0
    skipped = 0
    comp = company or prof.get('company') or _cfg('default_company')
    bank_account = (prof.get('bank_account') or '').strip()
    effective_account = (account or prof.get('account') or _cfg('privatbank_account') or '').strip()
    if not comp or not bank_account:
        frappe.throw(_("PrivatBank profile requires Company and ERP Bank Account"))
    bank_currency = validate_erp_bank_account(bank_account, comp)

    for row in rows:
        ref = str(_first_present(row, 'REF', 'ref', default='') or '').strip()
        refn = str(_first_present(row, 'REFN', 'refn', default='') or '').strip()
        tx_id = str(_first_present(row, 'id', 'ID', 'transactionId', 'TECHNICAL_TRANSACTION_ID', default='') or '').strip()
        if ref:
            tx_id = f'{ref}:{refn}' if refn else ref
        if not tx_id:
            skipped += 1
            continue

        integration_key = f"privatbank:{canonical_hash({'account': effective_account, 'tx_id': tx_id})}"
        exists = frappe.db.exists('Bank Transaction', {'ua_integration_key': integration_key})
        if exists:
            skipped += 1
            continue

        amount = _normalize_amount(_first_present(row, 'amount', 'sum', 'SUM', 'SUM_E', default=0), in_minor_units=prof.get('amount_in_minor_units', 1))
        if amount == 0:
            raise ValueError(f"PrivatBank transaction {tx_id} has a zero amount")

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

        description = str(row.get('description') or row.get('purpose') or row.get('OSND') or '')[:1000]
        transaction_currency = _currency_code(row.get('ccy') or row.get('CCY'), bank_currency)
        if transaction_currency != bank_currency:
            raise ValueError(
                f"PrivatBank transaction {tx_id} currency {transaction_currency} does not match Bank Account {bank_currency}"
            )

        doc = frappe.get_doc(
            {
                'doctype': 'Bank Transaction',
                'date': posting_date,
                'deposit': amount if amount > 0 else 0,
                'withdrawal': abs(amount) if amount < 0 else 0,
                'currency': bank_currency,
                'description': f'PBX:{tx_id[:140]} | {description}',
                'bank_account': bank_account,
                'company': comp,
                'ua_integration_key': integration_key,
            }
        )
        try:
            doc.insert(ignore_permissions=True)
            created += 1
        except frappe.DuplicateEntryError:
            if frappe.db.exists('Bank Transaction', {'ua_integration_key': integration_key}):
                skipped += 1
                continue
            raise

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
    require_roles(*ACCOUNTS_MANAGER_ROLES)
    prof = _pick_profile(profile)
    if not prof:
        frappe.throw(_("PrivatBank profile not found"))

    # According to docs, /settings may not include account list. Discover accounts via /balance without acc.
    today = date.today().strftime("%d-%m-%Y")
    out = _client(token=prof.get("token"), base_url=prof.get("api_base")).balances(
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
    require_roles(*ACCOUNTS_MANAGER_ROLES)
    if not account:
        frappe.throw(_("account is required"))
    d = frappe.get_single("PrivatBank Settings")
    d.check_permission("write")
    if not profile:
        frappe.throw(_("profile is required"))
    row = next((r for r in (d.get("profiles") or []) if r.get("name") == profile or r.get("label") == profile), None)
    if not row:
        frappe.throw(_("PrivatBank profile not found"))
    row.account = (account or "").strip()
    if bank_account is not None:
        row.bank_account = (bank_account or "").strip()
    d.save()
    return {"ok": True, "profile": profile, "account": row.account, "bank_account": row.get("bank_account")}
