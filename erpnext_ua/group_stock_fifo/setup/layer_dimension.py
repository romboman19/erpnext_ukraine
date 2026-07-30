"""The production layer Inventory Dimension and its cleanup patch (ADR-002).

Gate 0c proved the dimension does not steer valuation: it is an audit tag and a
per-layer negative-stock check, nothing more. Gate 0d proved
`apply_to_all_doctypes = 1` stamps the field onto every DocType with a
Link-to-Warehouse field — including the commission domain's, which GSF never
writes. ERPNext has no "these four DocTypes" scope (`document_type` is a single
Link), so ADR-002 keeps the wide registration and strips the fields afterwards.

The strip is deliberately keyed on *this app's* modules rather than a hardcoded
list of DocType names: a new commission DocType with a warehouse field would
otherwise reintroduce pollution the patch does not know about. It then asserts
its own result, so drift fails the migration instead of passing quietly.
"""

from __future__ import annotations

from typing import Any

DIMENSION_NAME = "GSF Stock Layer"
REFERENCE_DOCTYPE = "GSF Stock Layer"
#: On Stock Entry Detail the dimension is two fields, not one: the plain name
#: carries the outgoing leg (`s_warehouse`), the `to_` prefix the incoming one
#: (`t_warehouse`). Confusing them makes the *next* document fail on a negative
#: balance, not this one (gate 0d).
LAYER_FIELD = "gsf_stock_layer"
INCOMING_LAYER_FIELD = "to_gsf_stock_layer"

#: Set by GSF services on documents they build themselves. §17.3 refuses any
#: other document that touches a GSF pool, so this flag is what separates a
#: managed flow from a hand-written Stock Entry.
MANAGED_FIELD = "gsf_managed"

LAYER_BALANCE_INDEX = "gsf_layer_balance"
LAYER_FIFO_INDEX = "gsf_layer_fifo"

#: Where the layer tag has to survive for the chain proved in gates 0d and 0j to
#: be traceable end to end: reallocation legs, the ledger itself, and the sale.
REQUIRED_COLUMNS = {
    "Stock Entry Detail": (LAYER_FIELD, INCOMING_LAYER_FIELD),
    "Stock Ledger Entry": (LAYER_FIELD,),
    "Purchase Receipt Item": (LAYER_FIELD,),
    "Purchase Invoice Item": (LAYER_FIELD,),
    "Sales Invoice Item": (LAYER_FIELD,),
    "Stock Entry": (MANAGED_FIELD,),
    "Stock Reconciliation": (MANAGED_FIELD,),
}


def ensure_layer_dimension() -> None:
    """Create or verify the single production layer dimension, then clean up."""
    import frappe

    if not frappe.db.exists("DocType", REFERENCE_DOCTYPE):
        return

    _ensure_dimension(frappe)
    _ensure_managed_flag(frappe)
    frappe.clear_cache()
    _strip_foreign_fields(frappe)
    _assert_schema(frappe)
    _ensure_indexes(frappe)


def _ensure_managed_flag(frappe: Any) -> None:
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    create_custom_fields(
        {
            doctype: [
                {
                    "fieldname": MANAGED_FIELD,
                    "label": "GSF Managed",
                    "fieldtype": "Check",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                }
            ]
            for doctype in ("Stock Entry", "Stock Reconciliation")
        },
        update=True,
    )


def _ensure_dimension(frappe: Any) -> None:
    expected = {
        "reference_document": REFERENCE_DOCTYPE,
        "source_fieldname": LAYER_FIELD,
        "target_fieldname": LAYER_FIELD,
        "apply_to_all_doctypes": 1,
        "validate_negative_stock": 1,
    }
    if not frappe.db.exists("Inventory Dimension", DIMENSION_NAME):
        frappe.get_doc(
            {
                "doctype": "Inventory Dimension",
                "dimension_name": DIMENSION_NAME,
                "reference_document": REFERENCE_DOCTYPE,
                "apply_to_all_doctypes": 1,
                "validate_negative_stock": 1,
            }
        ).insert(ignore_permissions=True)
        return

    dimension = frappe.get_doc("Inventory Dimension", DIMENSION_NAME)
    mismatches = {
        fieldname: {"expected": value, "actual": dimension.get(fieldname)}
        for fieldname, value in expected.items()
        if str(dimension.get(fieldname) or "") != str(value)
    }
    if mismatches:
        raise RuntimeError(
            f"Existing {DIMENSION_NAME} Inventory Dimension is incompatible: {mismatches}"
        )


def _app_doctypes(frappe: Any) -> list[str]:
    """Every DocType shipped by this app, across all of its modules."""
    modules = frappe.get_all("Module Def", filters={"app_name": "erpnext_ua"}, pluck="name")
    if not modules:
        return []
    return frappe.get_all("DocType", filters={"module": ("in", modules)}, pluck="name")


def _strip_foreign_fields(frappe: Any) -> None:
    """ADR-002: GSF's field has no business on this app's own DocTypes.

    GSF tags ERPNext stock documents. Everything the wide registration reached
    inside `erpnext_ua` — the commission domain's DocTypes, and GSF's own
    registry, which merely happens to link a warehouse — is pollution: a column
    with an index, a migration cost and no writer.
    """
    doctypes = _app_doctypes(frappe)
    if not doctypes:
        return
    stray = frappe.get_all(
        "Custom Field",
        filters={"dt": ("in", doctypes), "fieldname": ("in", (LAYER_FIELD, INCOMING_LAYER_FIELD))},
        pluck="name",
    )
    for name in stray:
        frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True)
    if stray:
        frappe.clear_cache()


def _assert_schema(frappe: Any) -> None:
    """Fail the migration on drift rather than discover it during a sale."""
    missing = [
        f"{doctype}.{fieldname}"
        for doctype, fieldnames in REQUIRED_COLUMNS.items()
        for fieldname in fieldnames
        if not frappe.db.has_column(doctype, fieldname)
    ]
    if missing:
        raise RuntimeError(f"GSF layer dimension schema is incomplete: {missing}")

    doctypes = _app_doctypes(frappe)
    remaining = (
        frappe.get_all(
            "Custom Field",
            filters={
                "dt": ("in", doctypes),
                "fieldname": ("in", (LAYER_FIELD, INCOMING_LAYER_FIELD)),
            },
            fields=["dt", "fieldname"],
        )
        if doctypes
        else []
    )
    if remaining:
        raise RuntimeError(
            "GSF layer fields survived the ADR-002 cleanup on: "
            + ", ".join(f"{row.dt}.{row.fieldname}" for row in remaining)
        )


def _ensure_indexes(frappe: Any) -> None:
    """The two access paths that would otherwise scan: layer balance and FIFO order."""
    frappe.db.add_index(
        "Stock Ledger Entry",
        ["item_code", "warehouse", LAYER_FIELD, "is_cancelled"],
        LAYER_BALANCE_INDEX,
    )
    frappe.db.add_index(
        "GSF Stock Layer",
        ["company_group", "physical_location", "item_code", "layer_status", "original_received_datetime"],
        LAYER_FIFO_INDEX,
    )
