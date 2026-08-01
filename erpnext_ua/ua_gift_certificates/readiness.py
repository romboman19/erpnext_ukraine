from __future__ import annotations

import frappe

from .services.common import hmac_secret


def readiness_report(*, include_settings_state: bool = True) -> dict:
    checks = []

    def add(code, ok, message, blocking=True):
        checks.append(
            {"code": code, "status": "Passed" if ok else ("Blocked" if blocking else "Warning"), "message": message}
        )

    required = (
        "UA Gift Certificate",
        "UA Gift Certificate Ledger Entry",
        "UA Gift Certificate Reservation",
        "UA Gift Certificate Redemption Allocation",
    )
    add("GC_SCHEMA", all(frappe.db.table_exists(name) for name in required), "Core certificate tables are installed")
    try:
        hmac_secret(frappe.db.get_single_value("UA Gift Certificate Settings", "token_hmac_key_version") or "v1")
    except Exception:
        add("GC_HMAC_KEY", False, "HMAC key is not configured")
    else:
        add("GC_HMAC_KEY", True, "HMAC key is available")
    add(
        "GC_NETWORK",
        bool(frappe.db.exists("UA Gift Certificate Network", {"status": "Active"})),
        "An active Network exists",
    )
    add(
        "GC_PROGRAM",
        bool(frappe.db.exists("UA Gift Certificate Program", {"status": "Active"})),
        "An active Program exists",
    )
    add(
        "GC_ACCOUNTING",
        bool(frappe.db.exists("UA Gift Certificate Accounting Profile", {"status": "Active"})),
        "An active Accounting Profile exists",
    )
    add(
        "GC_COMPLIANCE",
        bool(frappe.db.exists("UA Gift Certificate Compliance Profile", {"status": "Active"})),
        "An active Compliance Profile exists",
    )
    add(
        "GC_PAYMENT_MODE",
        bool(frappe.db.exists("Mode of Payment", {"ua_gift_certificate_component": ("!=", "None")})),
        "Gift Certificate payment mappings exist",
    )
    add(
        "GC_STAGE0",
        frappe.db.get_single_value("UA Gift Certificate Settings", "stage0_status") == "Passed",
        "Stage 0 evidence is approved",
    )
    if include_settings_state:
        add(
            "GC_FEATURE_DISABLED_SAFE",
            not frappe.db.get_single_value("UA Gift Certificate Settings", "enabled"),
            "Feature remains disabled until readiness approval",
            blocking=False,
        )
    blocking = [row["code"] for row in checks if row["status"] == "Blocked"]
    warnings = [row["code"] for row in checks if row["status"] == "Warning"]
    return {
        "status": "Blocked" if blocking else ("Warning" if warnings else "Ready"),
        "checks": checks,
        "blocking_codes": blocking,
    }
