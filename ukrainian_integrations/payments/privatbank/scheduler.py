from __future__ import annotations

from datetime import date, timedelta

import frappe

from ukrainian_integrations.payments.privatbank.service import pb_statements_import_to_bank_transactions
from ukrainian_integrations.utils.logger import log_event


def run_auto_import() -> dict:
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
