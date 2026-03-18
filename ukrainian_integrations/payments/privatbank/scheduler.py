from __future__ import annotations

from datetime import date, timedelta

import frappe

from ukrainian_integrations.payments.privatbank.service import pb_statements_import_to_bank_transactions
from ukrainian_integrations.utils.logger import log_event


def _pb_settings() -> dict:
    if not frappe.db.exists("DocType", "PrivatBank Settings"):
        return {}
    try:
        d = frappe.get_single("PrivatBank Settings")
        return {
            "account": (d.get("account") or "").strip(),
            "company": (d.get("company") or "").strip(),
            "auto_import_enabled": int(d.get("auto_import_enabled") or 0),
            "auto_import_days_back": int(d.get("auto_import_days_back") or 1),
        }
    except Exception:
        return {}


def run_auto_import() -> dict:
    st = _pb_settings()
    enabled = int(st.get("auto_import_enabled") or frappe.conf.get("privatbank_auto_import_enabled", 0) or 0)
    if not enabled:
        return {"ok": True, "skipped": True, "reason": "disabled"}

    days_back = int(st.get("auto_import_days_back") or frappe.conf.get("privatbank_auto_import_days_back", 1) or 1)
    account = st.get("account") or frappe.conf.get("privatbank_account")
    company = st.get("company") or frappe.conf.get("default_company")

    end_date = date.today().isoformat()
    start_date = (date.today() - timedelta(days=max(0, days_back))).isoformat()

    try:
        out = pb_statements_import_to_bank_transactions(
            account=account,
            start_date=start_date,
            end_date=end_date,
            company=company,
        )
        log_event(
            "privatbank",
            "success",
            f"Auto import done for {start_date}..{end_date}",
            response_payload=out,
        )
        return out
    except Exception:
        log_event("privatbank", "error", "Auto import failed", error_trace=frappe.get_traceback())
        raise
