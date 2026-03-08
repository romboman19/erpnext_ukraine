from __future__ import annotations

import frappe

from ukrainian_integrations.payments.monobank.service import mono_statements_import_to_bank_transactions
from ukrainian_integrations.utils.logger import log_event


def run_auto_import() -> dict:
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
