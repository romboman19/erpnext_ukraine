"""The spike layer carrier DocType and its Inventory Dimension.

Kept apart from `stock_setup` because this module changes the site schema,
while that one only writes documents.
"""

from __future__ import annotations

from typing import Any

LAYER_DOCTYPE = "GSF Spike Layer"
# On Stock Entry Detail the dimension is two fields, not one: the plain name
# carries the outgoing leg and the `to_` prefix carries the incoming one.
DIMENSION_FIELD = "gsf_spike_layer"
INCOMING_DIMENSION_FIELD = "to_gsf_spike_layer"
CC_DIMENSION = "CC Stock Lot"


def ensure_layer_dimension(frappe: Any) -> dict[str, Any]:
    """Create the carrier DocType and its dimension once, then report the schema.

    The carrier is `custom = 1` and deliberately not named after the future
    production DocType, so a file-based one cannot collide with this leftover.
    """
    if not frappe.db.exists("DocType", LAYER_DOCTYPE):
        frappe.get_doc(
            {
                "doctype": "DocType",
                "name": LAYER_DOCTYPE,
                "module": "Stock",
                "custom": 1,
                "autoname": "GSF-LAYER-.#####",
                "fields": [
                    {"fieldname": "item_code", "fieldtype": "Link", "options": "Item", "label": "Item"},
                    {
                        "fieldname": "owner_company",
                        "fieldtype": "Link",
                        "options": "Company",
                        "label": "Owner Company",
                    },
                    {
                        "fieldname": "source_layer",
                        "fieldtype": "Link",
                        "options": LAYER_DOCTYPE,
                        "label": "Source Layer",
                    },
                ],
                "permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1}],
            }
        ).insert(ignore_permissions=True)

    if not frappe.db.exists("Inventory Dimension", LAYER_DOCTYPE):
        frappe.get_doc(
            {
                "doctype": "Inventory Dimension",
                "dimension_name": LAYER_DOCTYPE,
                "reference_document": LAYER_DOCTYPE,
                "apply_to_all_doctypes": 1,
                "validate_negative_stock": 1,
            }
        ).insert(ignore_permissions=True)
        frappe.clear_cache()

    return {
        "sle_column_present": bool(frappe.db.has_column("Stock Ledger Entry", DIMENSION_FIELD)),
        "cc_column_present": bool(frappe.db.has_column("Stock Ledger Entry", "cc_stock_lot")),
        "dimensions": sorted(frappe.db.sql_list("select name from `tabInventory Dimension`")),
    }


def new_layer(frappe: Any, *, item_code: str, company: str, source: str | None = None) -> str:
    doc = frappe.get_doc(
        {
            "doctype": LAYER_DOCTYPE,
            "item_code": item_code,
            "owner_company": company,
            "source_layer": source,
        }
    ).insert(ignore_permissions=True)
    return doc.name


def drop_layer_dimension(frappe: Any) -> list[str]:
    """Remove the dimension and its carrier. on_trash drops the custom fields."""
    removed = []
    for doctype, name in (("Inventory Dimension", LAYER_DOCTYPE), ("DocType", LAYER_DOCTYPE)):
        if frappe.db.exists(doctype, name):
            frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
            removed.append(f"{doctype} {name}")
    return removed
