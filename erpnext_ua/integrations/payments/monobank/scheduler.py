from __future__ import annotations

import frappe

from erpnext_ua.integrations.payments.monobank.service import (
    _mono_profiles,
    mono_statements_import_to_bank_transactions,
)
from erpnext_ua.integrations.utils.logger import log_event


def _mono_settings() -> dict:
    if not frappe.db.exists("DocType", "Monobank Settings"):
        return {}
    d = frappe.get_single("Monobank Settings")
    return {"enabled": int(d.get("enabled") or 0)}


def run_auto_import() -> dict:
    st = _mono_settings()
    if st and int(st.get("enabled") or 0) != 1:
        return {"ok": True, "skipped": True, "reason": "disabled"}
    # preferred: iterate profiles from child table
    profs = _mono_profiles()
    if profs:
        total_created = 0
        total_skipped = 0
        runs = 0
        failures = 0
        configured = [
            profile
            for profile in profs
            if int(profile.get("enabled") or 0) == 1
            and int(profile.get("auto_import_enabled") or 0) == 1
        ]
        for p in configured:
            try:
                out = mono_statements_import_to_bank_transactions(
                    account=p.get("account") or None,
                    days_back=int(p.get("auto_import_days_back") or 1),
                    company=p.get("company") or None,
                    profile=p.get("name") or p.get("label"),
                )
                total_created += int(out.get("created") or 0)
                total_skipped += int(out.get("skipped") or 0)
                runs += 1
            except Exception:
                failures += 1
                log_event("monobank", "error", f"Profile auto import failed: {p.get('label') or p.get('name')}", error_trace=frappe.get_traceback())
        if not configured:
            return {"ok": True, "skipped": True, "reason": "no_auto_import_profiles"}
        return {
            "ok": failures == 0,
            "profiles_runs": runs,
            "profiles_failed": failures,
            "created": total_created,
            "skipped": total_skipped,
        }

    enabled = int(frappe.conf.get("monobank_auto_import_enabled", 0) or 0)
    if not enabled:
        return {"ok": True, "skipped": True, "reason": "disabled"}

    days_back = int(frappe.conf.get("monobank_auto_import_days_back", 1) or 1)
    account = frappe.conf.get("monobank_account")
    company = frappe.conf.get("default_company")

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
