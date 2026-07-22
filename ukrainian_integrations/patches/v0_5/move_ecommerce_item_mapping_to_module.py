from __future__ import annotations

import frappe

DOCTYPE = "Ecommerce Item Mapping"
TARGET_MODULE = "Ecommerce"


def execute() -> None:
    """Move metadata before model sync so Frappe updates the existing table."""
    if not frappe.db.exists("DocType", DOCTYPE):
        return
    if frappe.db.get_value("DocType", DOCTYPE, "module") == TARGET_MODULE:
        return
    frappe.db.set_value(
        "DocType",
        DOCTYPE,
        "module",
        TARGET_MODULE,
        update_modified=False,
    )
    frappe.clear_cache(doctype=DOCTYPE)
