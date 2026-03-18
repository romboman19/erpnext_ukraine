from __future__ import annotations

from datetime import date, timedelta

import frappe

from ukrainian_integrations.payments.privatbank.service import pb_statements_import_to_bank_transactions, _pb_profiles
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
    profs = _pb_profiles()
    if profs:
        total_created = 0
        total_skipped = 0
        runs = 0
        for p in profs:
            if int(p.get("enabled") or 0) != 1 or int(p.get("auto_import_enabled") or 0) != 1:
                continue
            end_date = date.today().isoformat()
            start_date = (date.today() - timedelta(days=max(0, int(p.get("auto_import_days_back") or 1)))).isoformat()
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
        if runs:
            return {"ok": True, "profiles_runs": runs, "created": total_created, "skipped": total_skipped}

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
