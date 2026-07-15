from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta

import frappe
from frappe import _

from ukrainian_integrations.payments.monobank.client import MonobankClient
from ukrainian_integrations.utils.logger import log_event
from ukrainian_integrations.utils.operations import canonical_hash
from ukrainian_integrations.utils.security import ACCOUNTS_MANAGER_ROLES, require_roles
from ukrainian_integrations.utils.validation import validate_erp_bank_account


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _mono_profiles() -> list[dict]:
    if not frappe.db.exists("DocType", "Monobank Settings") or not frappe.db.exists("DocType", "Monobank Profile"):
        return []
    d = frappe.get_single("Monobank Settings")
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
                "account": (r.get("account") or "").strip(),
                "bank_account": (r.get("bank_account") or "").strip(),
                "company": (r.get("company") or "").strip(),
                "auto_import_enabled": int(r.get("auto_import_enabled") or 0),
                "auto_import_days_back": int(r.get("auto_import_days_back") or 1),
        })
    return out


def _pick_profile(profile: str | None = None) -> dict:
    profs = _mono_profiles()
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


def _mono_settings() -> dict:
    if not frappe.db.exists("DocType", "Monobank Settings"):
        return {}
    try:
        d = frappe.get_single("Monobank Settings")
        return {
            "enabled": int(d.get("enabled") or 0),
        }
    except Exception:
        return {}


def _client(token: str | None = None) -> MonobankClient:
    selected_token = _cfg("monobank_token") if token is None else token
    selected_token = (selected_token or "").strip()
    if not selected_token:
        frappe.throw(_("Не задано monobank_token у site_config.json"))
    return MonobankClient(selected_token)


def _to_unix_range(days_back: int = 1) -> tuple[int, int]:
    now = datetime.now(UTC)
    start = now - timedelta(days=max(0, int(days_back)))
    return int(start.timestamp()), int(now.timestamp())


@frappe.whitelist()
def mono_statements_fetch(account: str | None = None, from_ts: int | None = None, to_ts: int | None = None, days_back: int = 1, profile: str | None = None) -> dict:
    require_roles(*ACCOUNTS_MANAGER_ROLES)
    if _mono_profiles() and not _pick_profile(profile):
        frappe.throw(_("Enabled Monobank profile not found"))
    if frappe.db.exists("DocType", "Monobank Settings") and int(_mono_settings().get("enabled") or 0) != 1:
        frappe.throw(_("Monobank integration is disabled"))
    prof = _pick_profile(profile)
    acc = (account or prof.get("account") or _cfg("monobank_account") or "").strip()
    if not acc:
        frappe.throw(_("Не задано рахунок: передай account або monobank_account у site_config"))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", acc):
        frappe.throw(_("Monobank account ID is invalid"))

    if (from_ts is None) != (to_ts is None):
        frappe.throw(_("Both from_ts and to_ts must be provided together"))
    if from_ts is None:
        from_ts, to_ts = _to_unix_range(days_back=days_back)
    from_ts = int(from_ts)
    to_ts = int(to_ts)
    if from_ts < 0 or from_ts > to_ts:
        frappe.throw(_("Invalid Monobank statement time range"))
    if to_ts > int(datetime.now(UTC).timestamp()) + 300:
        frappe.throw(_("Monobank statement end time cannot be in the future"))
    if to_ts - from_ts > 31 * 24 * 60 * 60:
        frappe.throw(_("Monobank statement range cannot exceed 31 days"))

    req = {"account": acc, "from_ts": from_ts, "to_ts": to_ts}
    log_event("monobank", "queued", "Fetch statements", request_payload=req)
    try:
        rows = _client(token=(prof.get("token") if prof else None)).statements(account=acc, from_ts=from_ts, to_ts=to_ts)
        log_event("monobank", "success", f"Statements fetched: {len(rows)}", request_payload=req, response_payload={"count": len(rows)})
        return {"ok": True, "count": len(rows), "data": rows}
    except Exception:
        log_event("monobank", "error", "Fetch statements failed", request_payload=req, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def mono_statements_import_to_bank_transactions(account: str | None = None, from_ts: int | None = None, to_ts: int | None = None, days_back: int = 1, company: str | None = None, profile: str | None = None) -> dict:
    require_roles(*ACCOUNTS_MANAGER_ROLES)
    prof = _pick_profile(profile)
    fetched = mono_statements_fetch(account=account, from_ts=from_ts, to_ts=to_ts, days_back=days_back, profile=profile)
    rows = fetched.get("data") or []

    created = 0
    skipped = 0
    comp = company or prof.get("company") or _cfg("default_company")
    bank_account = (prof.get("bank_account") or "").strip()
    effective_account = (account or prof.get("account") or _cfg("monobank_account") or "").strip()
    if not comp or not bank_account:
        frappe.throw(_("Monobank profile requires Company and ERP Bank Account"))
    bank_currency = validate_erp_bank_account(bank_account, comp)

    for row in rows:
        tx_id = str(row.get("id") or row.get("statementItemId") or "").strip()
        if not tx_id:
            skipped += 1
            continue

        integration_key = f"monobank:{canonical_hash({'account': effective_account, 'tx_id': tx_id})}"
        exists = frappe.db.exists("Bank Transaction", {"ua_integration_key": integration_key})
        if exists:
            skipped += 1
            continue

        raw_amount = row.get("amount")
        if isinstance(raw_amount, bool) or not isinstance(raw_amount, int | float):
            raise ValueError(f"Monobank transaction {tx_id} has an invalid amount")
        amount = float(raw_amount) / 100.0
        if not math.isfinite(amount) or amount == 0:
            raise ValueError(f"Monobank transaction {tx_id} has an invalid zero/non-finite amount")
        ts = int(row.get("time") or 0)
        posting_date = datetime.fromtimestamp(ts, tz=UTC).date().isoformat() if ts else frappe.utils.nowdate()

        description = str(row.get("description") or row.get("comment") or "")[:1000]

        doc = frappe.get_doc(
            {
                "doctype": "Bank Transaction",
                "date": posting_date,
                "deposit": amount if amount > 0 else 0,
                "withdrawal": abs(amount) if amount < 0 else 0,
                "currency": bank_currency,
                "description": f"MBX:{tx_id[:140]} | {description}",
                "bank_account": bank_account,
                "company": comp,
                "ua_integration_key": integration_key,
            }
        )
        try:
            doc.insert(ignore_permissions=True)
            created += 1
        except frappe.DuplicateEntryError:
            if frappe.db.exists("Bank Transaction", {"ua_integration_key": integration_key}):
                skipped += 1
                continue
            raise

    log_event(
        "monobank",
        "success",
        f"Imported statements to Bank Transaction: created={created}, skipped={skipped}",
        request_payload={"account": account, "from_ts": from_ts, "to_ts": to_ts, "days_back": days_back},
        response_payload={"created": created, "skipped": skipped},
    )
    return {"ok": True, "created": created, "skipped": skipped}


@frappe.whitelist()
def mono_list_accounts(profile: str | None = None) -> dict:
    require_roles(*ACCOUNTS_MANAGER_ROLES)
    prof = _pick_profile(profile)
    if _mono_profiles() and not prof:
        frappe.throw(_("Enabled Monobank profile not found"))
    info = _client(token=(prof.get("token") if prof else None)).client_info()
    accounts = info.get("accounts") or []
    out = []
    for a in accounts:
        out.append({
            "id": a.get("id"),
            "iban": a.get("iban"),
            "currencyCode": a.get("currencyCode"),
            "type": a.get("type"),
            "maskedPan": a.get("maskedPan") or [],
            "label": f"{a.get('id')} | {a.get('iban') or ''} | {a.get('currencyCode')} | {a.get('type')}",
        })
    return {"ok": True, "count": len(out), "accounts": out}


@frappe.whitelist()
def mono_bind_account(account_id: str, bank_account: str | None = None, profile: str | None = None) -> dict:
    require_roles(*ACCOUNTS_MANAGER_ROLES)
    if not account_id:
        frappe.throw(_("account_id is required"))
    if not frappe.db.exists("DocType", "Monobank Settings"):
        frappe.throw(_("Monobank Settings not found"))

    d = frappe.get_single("Monobank Settings")
    d.check_permission("write")
    if profile and frappe.db.exists("DocType", "Monobank Profile"):
        row = next((r for r in (d.get("profiles") or []) if r.get("name") == profile or r.get("label") == profile), None)
        if not row:
            frappe.throw(_("Monobank profile not found"))
        row.account = (account_id or "").strip()
        if bank_account is not None:
            row.bank_account = (bank_account or "").strip()
    else:
        frappe.throw(_("Profile is required"))

    d.save()
    return {"ok": True, "account": account_id, "bank_account": bank_account, "profile": profile}
