from __future__ import annotations

import frappe

FIELDNAME = "ua_ecommerce_channel"
LEGACY_OPTIONS = "Ecommerce Channel"
TARGET_DOCTYPES = ("Sales Order", "Sales Invoice")


def execute() -> None:
    """Preserve legacy values while removing their universal-DocType link."""
    for doctype in TARGET_DOCTYPES:
        name = frappe.db.get_value(
            "Custom Field",
            {"dt": doctype, "fieldname": FIELDNAME},
            "name",
        )
        if not name:
            continue
        state = frappe.db.get_value(
            "Custom Field",
            name,
            ["fieldtype", "options"],
            as_dict=True,
        )
        if state.fieldtype == "Data":
            continue
        if state.fieldtype != "Link" or state.options != LEGACY_OPTIONS:
            raise RuntimeError(
                f"Cannot migrate unexpected custom field contract: {doctype}.{FIELDNAME} "
                f"({state.fieldtype} -> {state.options})"
            )
        # Link and Data both use the same varchar column. Updating metadata
        # directly avoids Frappe's unsafe generic fieldtype-change rejection
        # without modifying any persisted provider-instance value.
        frappe.db.set_value(
            "Custom Field",
            name,
            {"fieldtype": "Data", "options": None},
            update_modified=False,
        )
        frappe.clear_cache(doctype=doctype)
