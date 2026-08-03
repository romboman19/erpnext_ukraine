from __future__ import annotations

import frappe


def settings():
    if not frappe.db.exists("DocType", "UA Loyalty Settings"):
        return frappe._dict(enabled=0, execution_mode="DISABLED")
    return frappe.get_cached_doc("UA Loyalty Settings")


def enabled_for(source_doctype: str = "POS Order") -> bool:
    config = settings()
    if not config.enabled or config.execution_mode in {"DISABLED", "READ_ONLY"}:
        return False
    if source_doctype == "POS Order":
        return config.execution_mode in {"POS_ONLY", "POS_AND_SALES_INVOICE"}
    return config.execution_mode == "POS_AND_SALES_INVOICE" and bool(config.allow_manual_sales_invoice)
