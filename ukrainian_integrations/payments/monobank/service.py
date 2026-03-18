from __future__ import annotations

from datetime import datetime, timedelta, timezone

import frappe
from frappe import _

from ukrainian_integrations.payments.monobank.client import MonobankClient
from ukrainian_integrations.utils.logger import log_event


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _mono_settings() -> dict:
    if not frappe.db.exists("DocType", "Monobank Settings"):
        return {}
    try:
        d = frappe.get_single("Monobank Settings")
        return {
            "enabled": int(d.get("enabled") or 0),
            "token": (d.get_password("token") or "").strip(),
            "account": (d.get("account") or "").strip(),
            "company": (d.get("company") or "").strip(),
            "auto_import_enabled": int(d.get("auto_import_enabled") or 0),
            "auto_import_days_back": int(d.get("auto_import_days_back") or 1),
        }
    except Exception:
        return {}


def _client() -> MonobankClient:
    st = _mono_settings()
    token = st.get("token") or _cfg("monobank_token")
    if not token:
        frappe.throw(_("Не задано monobank_token у site_config.json"))
    return MonobankClient(token)


def _to_unix_range(days_back: int = 1) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=max(0, int(days_back)))
    return int(start.timestamp()), int(now.timestamp())


@frappe.whitelist()
def mono_statements_fetch(account: str | None = None, from_ts: int | None = None, to_ts: int | None = None, days_back: int = 1) -> dict:
    st = _mono_settings()
    acc = (account or st.get("account") or _cfg("monobank_account") or "").strip()
    if not acc:
        frappe.throw(_("Не задано рахунок: передай account або monobank_account у site_config"))

    if from_ts is None or to_ts is None:
        from_ts, to_ts = _to_unix_range(days_back=days_back)

    req = {"account": acc, "from_ts": int(from_ts), "to_ts": int(to_ts)}
    log_event("monobank", "queued", "Fetch statements", request_payload=req)
    try:
        rows = _client().statements(account=acc, from_ts=int(from_ts), to_ts=int(to_ts))
        log_event("monobank", "success", f"Statements fetched: {len(rows)}", request_payload=req, response_payload={"count": len(rows)})
        return {"ok": True, "count": len(rows), "data": rows}
    except Exception:
        log_event("monobank", "error", "Fetch statements failed", request_payload=req, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def mono_statements_import_to_bank_transactions(account: str | None = None, from_ts: int | None = None, to_ts: int | None = None, days_back: int = 1, company: str | None = None) -> dict:
    fetched = mono_statements_fetch(account=account, from_ts=from_ts, to_ts=to_ts, days_back=days_back)
    rows = fetched.get("data") or []

    created = 0
    skipped = 0
    comp = company or _cfg("default_company")

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
                "bank_account_no": (account or _cfg("monobank_account") or ""),
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
