from __future__ import annotations

import frappe

from ukrainian_integrations.payments.monobank.scheduler import run_auto_import as mono_auto_import
from ukrainian_integrations.payments.privatbank.scheduler import run_auto_import as privat_auto_import
from ukrainian_integrations.utils.logger import log_event


def run_all_bank_imports() -> dict:
    summary = {
        "ok": True,
        "providers": {},
    }

    for name, fn in (("privatbank", privat_auto_import), ("monobank", mono_auto_import)):
        try:
            out = fn()
            summary["providers"][name] = out
        except Exception:
            summary["providers"][name] = {
                "ok": False,
                "error": "exception",
            }
            summary["ok"] = False
            log_event("bank_import", "error", f"{name} auto import failed", error_trace=frappe.get_traceback())

    log_event("bank_import", "success" if summary["ok"] else "error", "Bank imports summary", response_payload=summary)
    return summary
