from __future__ import annotations

from datetime import date, timedelta

import frappe

from ukrainian_integrations.payments.privatbank.service import _pb_profiles, pb_statements_import_to_bank_transactions
from ukrainian_integrations.utils.logger import log_event


def _pb_settings() -> dict:
    if not frappe.db.exists("DocType", "PrivatBank Settings"):
        return {}
    d = frappe.get_single("PrivatBank Settings")
    return {"enabled": int(d.get("enabled") or 0)}


def run_auto_import() -> dict:
    settings = _pb_settings()
    if settings and int(settings.get("enabled") or 0) != 1:
        return {"ok": True, "skipped": True, "reason": "disabled"}
    profs = _pb_profiles()
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
            end_date = date.today().isoformat()
            start_date = (date.today() - timedelta(days=max(0, int(p.get("auto_import_days_back") or 1)))).isoformat()
            try:
                out = pb_statements_import_to_bank_transactions(
                    account=p.get("account") or None,
                    start_date=start_date,
                    end_date=end_date,
                    company=p.get("company") or None,
                    profile=p.get("name") or p.get("label"),
                )
                total_created += int(out.get("created") or 0)
                total_skipped += int(out.get("skipped") or 0)
                runs += 1
            except Exception:
                failures += 1
                log_event("privatbank", "error", f"Profile auto import failed: {p.get('label') or p.get('name')}", error_trace=frappe.get_traceback())
        if not configured:
            return {"ok": True, "skipped": True, "reason": "no_auto_import_profiles"}
        return {
            "ok": failures == 0,
            "profiles_runs": runs,
            "profiles_failed": failures,
            "created": total_created,
            "skipped": total_skipped,
        }

    enabled = int(frappe.conf.get("privatbank_auto_import_enabled", 0) or 0)
    if not enabled:
        return {"ok": True, "skipped": True, "reason": "disabled"}

    days_back = int(frappe.conf.get("privatbank_auto_import_days_back", 1) or 1)
    account = frappe.conf.get("privatbank_account")
    company = frappe.conf.get("default_company")

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
