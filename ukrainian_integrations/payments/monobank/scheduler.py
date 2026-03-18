from __future__ import annotations

import frappe

from ukrainian_integrations.payments.monobank.service import mono_statements_import_to_bank_transactions, _mono_profiles
from ukrainian_integrations.utils.logger import log_event


def _mono_settings() -> dict:
    if not frappe.db.exists("DocType", "Monobank Settings"):
        return {}
    try:
        d = frappe.get_single("Monobank Settings")
        return {
            "enabled": int(d.get("enabled") or 0),
            "account": (d.get("account") or "").strip(),
            "company": (d.get("company") or "").strip(),
            "auto_import_enabled": int(d.get("auto_import_enabled") or 0),
            "auto_import_days_back": int(d.get("auto_import_days_back") or 1),
        }
    except Exception:
        return {}


def run_auto_import() -> dict:
    # preferred: iterate profiles from child table
    profs = _mono_profiles()
    if profs:
        total_created = 0
        total_skipped = 0
        runs = 0
        for p in profs:
            if int(p.get("enabled") or 0) != 1 or int(p.get("auto_import_enabled") or 0) != 1:
                continue
            out = mono_statements_import_to_bank_transactions(
                account=p.get("account") or None,
                days_back=int(p.get("auto_import_days_back") or 1),
                company=p.get("company") or None,
                profile=p.get("name") or p.get("label"),
            )
            total_created += int(out.get("created") or 0)
            total_skipped += int(out.get("skipped") or 0)
            runs += 1
        if runs:
            return {"ok": True, "profiles_runs": runs, "created": total_created, "skipped": total_skipped}

    st = _mono_settings()
    enabled = int(st.get("auto_import_enabled") or frappe.conf.get("monobank_auto_import_enabled", 0) or 0)
    if not enabled:
        return {"ok": True, "skipped": True, "reason": "disabled"}

    days_back = int(st.get("auto_import_days_back") or frappe.conf.get("monobank_auto_import_days_back", 1) or 1)
    account = st.get("account") or frappe.conf.get("monobank_account")
    company = st.get("company") or frappe.conf.get("default_company")

    try:
        out = mono_statements_import_to_bank_transactions(
            account=account,
            days_back=days_back,
            company=company,
        )
        log_event("monobank", "success", f"Auto import done (days_back={days_back})", response_payload=out)
        return out
    except Exception:
        log_event("monobank", "error", "Auto import failed", error_trace=frappe.get_traceback())
        raise
