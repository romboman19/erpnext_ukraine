from __future__ import annotations

from datetime import datetime, timedelta, timezone

import frappe
from frappe import _

from ukrainian_integrations.payments.monobank.client import MonobankClient
from ukrainian_integrations.utils.logger import log_event


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _mono_profiles() -> list[dict]:
    if not frappe.db.exists("DocType", "Monobank Settings") or not frappe.db.exists("DocType", "Monobank Profile"):
        return []
    try:
        d = frappe.get_single("Monobank Settings")
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
                "account": (r.get("account") or "").strip(),
                "bank_account": (r.get("bank_account") or "").strip(),
                "company": (r.get("company") or "").strip(),
                "auto_import_enabled": int(r.get("auto_import_enabled") or 0),
                "auto_import_days_back": int(r.get("auto_import_days_back") or 1),
            })
        return out
    except Exception:
        return []


def _pick_profile(profile: str | None = None) -> dict:
    profs = _mono_profiles()
    if not profs:
        return {}
    if profile:
        p = next((x for x in profs if (x.get("name") == profile or x.get("label") == profile)), None)
        if p:
            return p
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
    token = (token or _cfg("monobank_token") or "").strip()
    if not token:
        frappe.throw(_("Не задано monobank_token у site_config.json"))
    return MonobankClient(token)


def _to_unix_range(days_back: int = 1) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=max(0, int(days_back)))
    return int(start.timestamp()), int(now.timestamp())


@frappe.whitelist()
def mono_statements_fetch(account: str | None = None, from_ts: int | None = None, to_ts: int | None = None, days_back: int = 1, profile: str | None = None) -> dict:
    prof = _pick_profile(profile)
    acc = (account or prof.get("account") or _cfg("monobank_account") or "").strip()
    if not acc:
        frappe.throw(_("Не задано рахунок: передай account або monobank_account у site_config"))

    if from_ts is None or to_ts is None:
        from_ts, to_ts = _to_unix_range(days_back=days_back)

    req = {"account": acc, "from_ts": int(from_ts), "to_ts": int(to_ts)}
    log_event("monobank", "queued", "Fetch statements", request_payload=req)
    try:
        rows = _client(token=(prof.get("token") or None)).statements(account=acc, from_ts=int(from_ts), to_ts=int(to_ts))
        log_event("monobank", "success", f"Statements fetched: {len(rows)}", request_payload=req, response_payload={"count": len(rows)})
        return {"ok": True, "count": len(rows), "data": rows}
    except Exception:
        log_event("monobank", "error", "Fetch statements failed", request_payload=req, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def mono_statements_import_to_bank_transactions(account: str | None = None, from_ts: int | None = None, to_ts: int | None = None, days_back: int = 1, company: str | None = None, profile: str | None = None) -> dict:
    prof = _pick_profile(profile)
    fetched = mono_statements_fetch(account=account, from_ts=from_ts, to_ts=to_ts, days_back=days_back, profile=profile)
    rows = fetched.get("data") or []

    created = 0
    skipped = 0
    comp = company or prof.get("company") or _cfg("default_company")

    for row in rows:
        tx_id = str(row.get("id") or row.get("statementItemId") or "").strip()
        if not tx_id:
            skipped += 1
            continue

        exists = frappe.db.exists("Bank Transaction", {"description": ["like", f"%MBX:{tx_id}%"]})
        if exists:
            skipped += 1
            continue

        amount = float(row.get("amount") or 0) / 100.0
        ts = int(row.get("time") or 0)
        posting_date = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat() if ts else frappe.utils.nowdate()

        description = row.get("description") or row.get("comment") or ""

        doc = frappe.get_doc(
            {
                "doctype": "Bank Transaction",
                "date": posting_date,
                "deposit": amount if amount > 0 else 0,
                "withdrawal": abs(amount) if amount < 0 else 0,
                "currency": "UAH",
                "description": f"MBX:{tx_id} | {description}",
                "bank_account": (prof.get("bank_account") or ""),
                "bank_account_no": (account or prof.get("account") or _cfg("monobank_account") or ""),
                "company": comp,
            }
        )
        doc.insert(ignore_permissions=True)
        created += 1

    if created:
        frappe.db.commit()

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
    prof = _pick_profile(profile)
    info = _client(token=(prof.get("token") or None)).client_info()
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
    if not account_id:
        frappe.throw(_("account_id is required"))
    if not frappe.db.exists("DocType", "Monobank Settings"):
        frappe.throw(_("Monobank Settings not found"))

    d = frappe.get_single("Monobank Settings")
    if profile and frappe.db.exists("DocType", "Monobank Profile"):
        row = next((r for r in (d.get("profiles") or []) if r.get("name") == profile or r.get("label") == profile), None)
        if not row:
            frappe.throw(_("Monobank profile not found"))
        row.account = (account_id or "").strip()
        if bank_account is not None:
            row.bank_account = (bank_account or "").strip()
    else:
        frappe.throw(_("Profile is required"))

    d.save(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True, "account": account_id, "bank_account": bank_account, "profile": profile}
