"""Idempotent ownership Inventory Dimension and audit-link schema."""

from __future__ import annotations

from typing import Any

DIMENSION_NAME = "CC Stock Lot"
REFERENCE_DOCTYPE = "CC Stock Lot"
OWNERSHIP_FIELD = "cc_stock_lot"
RECEIPT_FIELD = "cc_receipt"
RECEIPT_ITEM_FIELD = "cc_receipt_item"
PARTNER_RETURN_FIELD = "cc_partner_return"
OWNERSHIP_CONVERSION_FIELD = "cc_ownership_conversion"
OWN_RECEIPT_FIELD = "cc_own_receipt"
OWN_RECEIPT_ITEM_FIELD = "cc_own_receipt_item"
BALANCE_INDEX = "cc_stock_lot_balance"
ALLOCATION_EXPIRY_INDEX = "cc_allocation_expiry"
ALLOCATION_SERIAL_INDEX = "cc_allocation_serial"
STOCK_LOT_FIFO_INDEX = "cc_stock_lot_fifo"
SALE_FINANCIAL_INDEX = "cc_sale_financial"
SALE_REPORT_INDEX = "cc_sale_report"
RETURN_AUDIT_INDEX = "cc_return_audit"
SETTLEMENT_DUE_INDEX = "cc_settlement_due"
POS_ROUTE_QUEUE_INDEX = "cc_pos_route_queue"
POS_PRINT_QUEUE_INDEX = "cc_pos_print_queue"
POS_CHECKOUT_QUEUE_INDEX = "cc_pos_checkout_queue"
MANAGED_SALE_FIELD = "cc_managed_sale"
MANAGED_RETURN_FIELD = "cc_managed_return"
SALE_IDEMPOTENCY_FIELD = "cc_sale_idempotency_key"
SALE_FINGERPRINT_FIELD = "cc_sale_fingerprint"
RETURN_IDEMPOTENCY_FIELD = "cc_return_idempotency_key"
RETURN_FINGERPRINT_FIELD = "cc_return_fingerprint"
RETURN_SALE_ALLOCATION_FIELD = "cc_return_sale_allocation"
ALLOCATION_FIELD = "cc_allocation"
ALLOCATION_SLICE_FIELD = "cc_allocation_slice"
SOURCE_METHOD_FIELD = "cc_source_method"
RELATIONSHIP_MODEL_FIELD = "cc_relationship_model"
SALES_INVOICE_FIELD = "cc_sales_invoice"
SETTLEMENT_REPORT_FIELD = "cc_settlement_report"
POSTING_KIND_FIELD = "cc_posting_kind"
PAYMENT_IDEMPOTENCY_FIELD = "cc_payment_idempotency_key"
PAYMENT_FINGERPRINT_FIELD = "cc_payment_fingerprint"
SETTLEMENT_ADJUSTMENT_FIELD = "cc_settlement_adjustment"
POS_CHECKOUT_FIELD = "cc_pos_checkout"
POS_ROUTE_FIELD = "cc_pos_route"
POS_ORDER_FIELD = "cc_pos_order"
LEGACY_POS_ROUTE_GROUP_INDEX = "group_id"


def _ensure_audit_fields(frappe: Any) -> None:
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    create_custom_fields(
        {
            "Stock Entry": [
                {
                    "fieldname": RECEIPT_FIELD,
                    "label": "CC Receipt",
                    "fieldtype": "Link",
                    "options": "CC Receipt",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                    "unique": 1,
                },
                {
                    "fieldname": PARTNER_RETURN_FIELD,
                    "label": "CC Partner Return",
                    "fieldtype": "Link",
                    "options": "CC Partner Return",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                    "unique": 1,
                },
                {
                    "fieldname": OWNERSHIP_CONVERSION_FIELD,
                    "label": "CC Ownership Conversion",
                    "fieldtype": "Link",
                    "options": "CC Ownership Conversion",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                    "unique": 1,
                },
            ],
            "Stock Entry Detail": [
                {
                    "fieldname": RECEIPT_ITEM_FIELD,
                    "label": "CC Receipt Item Row",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                }
            ],
            "Purchase Invoice": [
                {
                    "fieldname": OWN_RECEIPT_FIELD,
                    "label": "CC Own Receipt",
                    "fieldtype": "Link",
                    "options": "CC Own Receipt",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                    "unique": 1,
                },
                {
                    "fieldname": OWNERSHIP_CONVERSION_FIELD,
                    "label": "CC Ownership Conversion",
                    "fieldtype": "Link",
                    "options": "CC Ownership Conversion",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                    "unique": 1,
                },
            ],
            "Purchase Invoice Item": [
                {
                    "fieldname": OWN_RECEIPT_ITEM_FIELD,
                    "label": "CC Own Receipt Item Row",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                }
            ],
            "Sales Invoice": [
                {
                    "fieldname": MANAGED_SALE_FIELD,
                    "label": "CC Managed Sale",
                    "fieldtype": "Check",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": MANAGED_RETURN_FIELD,
                    "label": "CC Managed Return",
                    "fieldtype": "Check",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": SALE_IDEMPOTENCY_FIELD,
                    "label": "CC Sale Idempotency Key",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                    "unique": 1,
                },
                {
                    "fieldname": SALE_FINGERPRINT_FIELD,
                    "label": "CC Sale Request Fingerprint",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "no_copy": 1,
                    "hidden": 1,
                },
                {
                    "fieldname": RETURN_IDEMPOTENCY_FIELD,
                    "label": "CC Return Idempotency Key",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                    "unique": 1,
                },
                {
                    "fieldname": RETURN_FINGERPRINT_FIELD,
                    "label": "CC Return Fingerprint",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "no_copy": 1,
                },
                {
                    "fieldname": POS_CHECKOUT_FIELD,
                    "label": "CC POS Checkout",
                    "fieldtype": "Link",
                    "options": "CC POS Checkout",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": POS_ROUTE_FIELD,
                    "label": "CC POS Route",
                    "fieldtype": "Link",
                    "options": "CC POS Route",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                    "unique": 1,
                },
                {
                    "fieldname": POS_ORDER_FIELD,
                    "label": "External POS Order",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                },
            ],
            "Sales Invoice Item": [
                {
                    "fieldname": RETURN_SALE_ALLOCATION_FIELD,
                    "label": "CC Original Sale Allocation",
                    "fieldtype": "Link",
                    "options": "CC Sale Allocation",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": ALLOCATION_FIELD,
                    "label": "CC Allocation",
                    "fieldtype": "Link",
                    "options": "CC Allocation",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": ALLOCATION_SLICE_FIELD,
                    "label": "CC Allocation Slice",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": SOURCE_METHOD_FIELD,
                    "label": "CC Source Method",
                    "fieldtype": "Select",
                    "options": "BUYOUT\nDEFERRED_PURCHASE\nCOMMISSION\nCONSIGNMENT",
                    "read_only": 1,
                    "no_copy": 1,
                },
                {
                    "fieldname": RELATIONSHIP_MODEL_FIELD,
                    "label": "CC Relationship Model",
                    "fieldtype": "Select",
                    "options": "OWN\nCOMMISSION\nCONSIGNMENT",
                    "read_only": 1,
                    "no_copy": 1,
                },
            ],
            "Journal Entry": [
                {
                    "fieldname": SALES_INVOICE_FIELD,
                    "label": "CC Sales Invoice",
                    "fieldtype": "Link",
                    "options": "Sales Invoice",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                    "unique": 1,
                },
                {
                    "fieldname": SETTLEMENT_REPORT_FIELD,
                    "label": "CC Settlement Report",
                    "fieldtype": "Link",
                    "options": "CC Settlement Report",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                    "unique": 1,
                },
                {
                    "fieldname": POSTING_KIND_FIELD,
                    "label": "CC Posting Kind",
                    "fieldtype": "Select",
                    "options": (
                        "SALE_RECOGNITION\nRETURN_REVERSAL\nSETTLEMENT_DEBT\n"
                        "SETTLEMENT_ADJUSTMENT\nADJUSTMENT"
                    ),
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": SETTLEMENT_ADJUSTMENT_FIELD,
                    "label": "CC Settlement Adjustment",
                    "fieldtype": "Link",
                    "options": "CC Settlement Adjustment",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                    "unique": 1,
                },
            ],
            "Payment Entry": [
                {
                    "fieldname": SETTLEMENT_REPORT_FIELD,
                    "label": "CC Settlement Report",
                    "fieldtype": "Link",
                    "options": "CC Settlement Report",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": PAYMENT_IDEMPOTENCY_FIELD,
                    "label": "CC Payment Idempotency Key",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                    "unique": 1,
                },
                {
                    "fieldname": PAYMENT_FINGERPRINT_FIELD,
                    "label": "CC Payment Fingerprint",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "no_copy": 1,
                },
            ],
            "Batch": [
                {
                    "fieldname": OWNERSHIP_FIELD,
                    "label": "CC Stock Lot",
                    "fieldtype": "Link",
                    "options": REFERENCE_DOCTYPE,
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                }
            ],
            "Serial No": [
                {
                    "fieldname": OWNERSHIP_FIELD,
                    "label": "CC Stock Lot",
                    "fieldtype": "Link",
                    "options": REFERENCE_DOCTYPE,
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                }
            ],
        },
        update=True,
    )


def ensure_ownership_dimension() -> None:
    """Create or verify the single production ownership dimension."""
    import frappe

    if not frappe.db.exists("DocType", REFERENCE_DOCTYPE):
        return

    if frappe.db.exists("DocType", "CC POS Route"):
        from frappe.database.utils import drop_index_if_exists

        table = "tabCC POS Route"
        if frappe.db.has_index(table, LEGACY_POS_ROUTE_GROUP_INDEX):
            drop_index_if_exists(table, LEGACY_POS_ROUTE_GROUP_INDEX)
            if frappe.db.has_index(table, LEGACY_POS_ROUTE_GROUP_INDEX):
                raise RuntimeError(
                    "Legacy global CC POS Route.group_id unique index could not be removed"
                )

    if frappe.db.exists("Inventory Dimension", DIMENSION_NAME):
        dimension = frappe.get_doc("Inventory Dimension", DIMENSION_NAME)
        expected = {
            "reference_document": REFERENCE_DOCTYPE,
            "source_fieldname": OWNERSHIP_FIELD,
            "target_fieldname": OWNERSHIP_FIELD,
            "apply_to_all_doctypes": 1,
            "validate_negative_stock": 1,
        }
        mismatches = {
            fieldname: {"expected": value, "actual": dimension.get(fieldname)}
            for fieldname, value in expected.items()
            if str(dimension.get(fieldname) or "") != str(value)
        }
        if mismatches:
            raise RuntimeError(f"Existing CC Stock Lot Inventory Dimension is incompatible: {mismatches}")
    else:
        frappe.get_doc(
            {
                "doctype": "Inventory Dimension",
                "dimension_name": DIMENSION_NAME,
                "reference_document": REFERENCE_DOCTYPE,
                "apply_to_all_doctypes": 1,
                "validate_negative_stock": 1,
            }
        ).insert(ignore_permissions=True)

    _ensure_audit_fields(frappe)
    if frappe.db.has_column("CC Stock Lot", "source_method"):
        frappe.db.sql(
            """
            update `tabCC Stock Lot`
            set source_method = relationship_model
            where (source_method is null or source_method = '')
              and relationship_model in ('COMMISSION', 'CONSIGNMENT')
            """
        )
    frappe.clear_cache()
    required_columns = {
        "Stock Entry Detail": (OWNERSHIP_FIELD, f"to_{OWNERSHIP_FIELD}", RECEIPT_ITEM_FIELD),
        "Stock Ledger Entry": (OWNERSHIP_FIELD,),
        "Stock Entry": (
            RECEIPT_FIELD,
            PARTNER_RETURN_FIELD,
            OWNERSHIP_CONVERSION_FIELD,
        ),
        "Purchase Invoice": (OWN_RECEIPT_FIELD, OWNERSHIP_CONVERSION_FIELD),
        "Purchase Invoice Item": (OWN_RECEIPT_ITEM_FIELD, OWNERSHIP_FIELD),
        "Sales Invoice": (
            MANAGED_SALE_FIELD,
            MANAGED_RETURN_FIELD,
            SALE_IDEMPOTENCY_FIELD,
            SALE_FINGERPRINT_FIELD,
            RETURN_IDEMPOTENCY_FIELD,
            RETURN_FINGERPRINT_FIELD,
            POS_CHECKOUT_FIELD,
            POS_ROUTE_FIELD,
            POS_ORDER_FIELD,
        ),
        "Sales Invoice Item": (
            OWNERSHIP_FIELD,
            RETURN_SALE_ALLOCATION_FIELD,
            ALLOCATION_FIELD,
            ALLOCATION_SLICE_FIELD,
            SOURCE_METHOD_FIELD,
            RELATIONSHIP_MODEL_FIELD,
        ),
        "Journal Entry": (
            SALES_INVOICE_FIELD,
            SETTLEMENT_REPORT_FIELD,
            SETTLEMENT_ADJUSTMENT_FIELD,
            POSTING_KIND_FIELD,
        ),
        "Payment Entry": (
            SETTLEMENT_REPORT_FIELD,
            PAYMENT_IDEMPOTENCY_FIELD,
            PAYMENT_FINGERPRINT_FIELD,
        ),
        "Batch": (OWNERSHIP_FIELD,),
        "Serial No": (OWNERSHIP_FIELD,),
    }
    missing = [
        f"{doctype}.{fieldname}"
        for doctype, fieldnames in required_columns.items()
        for fieldname in fieldnames
        if not frappe.db.has_column(doctype, fieldname)
    ]
    if missing:
        raise RuntimeError(f"CC ownership schema is incomplete after synchronization: {missing}")

    frappe.db.add_index(
        "Stock Ledger Entry",
        ["item_code", "warehouse", OWNERSHIP_FIELD, "is_cancelled"],
        BALANCE_INDEX,
    )
    if frappe.db.exists("DocType", "CC Allocation"):
        frappe.db.add_index(
            "CC Allocation",
            ["status", "expires_at"],
            ALLOCATION_EXPIRY_INDEX,
        )
        frappe.db.add_index(
            "CC Allocation Slice",
            ["stock_lot", "serial_no"],
            ALLOCATION_SERIAL_INDEX,
        )
    if frappe.db.exists("DocType", "CC Stock Lot"):
        frappe.db.add_index(
            "CC Stock Lot",
            ["company", "location", "item_code", "lot_status", "received_datetime"],
            STOCK_LOT_FIFO_INDEX,
        )
    if frappe.db.exists("DocType", "CC Sale Allocation"):
        frappe.db.add_index(
            "CC Sale Allocation",
            ["company", "posting_date", "supplier", "status"],
            SALE_FINANCIAL_INDEX,
        )
        frappe.db.add_index(
            "CC Sale Allocation",
            ["settlement_report", "status"],
            SALE_REPORT_INDEX,
        )
    if frappe.db.exists("DocType", "CC Sale Return Allocation"):
        frappe.db.add_index(
            "CC Sale Return Allocation",
            ["sale_allocation", "status"],
            RETURN_AUDIT_INDEX,
        )
    if frappe.db.exists("DocType", "CC Settlement Report"):
        frappe.db.add_index(
            "CC Settlement Report",
            ["company", "docstatus", "due_date", "status"],
            SETTLEMENT_DUE_INDEX,
        )
    if frappe.db.exists("DocType", "CC POS Checkout"):
        frappe.db.add_index(
            "CC POS Checkout",
            ["status", "creation"],
            POS_CHECKOUT_QUEUE_INDEX,
        )
    if frappe.db.exists("DocType", "CC POS Route"):
        frappe.db.add_index(
            "CC POS Route",
            ["checkout", "status"],
            POS_ROUTE_QUEUE_INDEX,
        )
    if frappe.db.exists("DocType", "CC POS Print Job"):
        frappe.db.add_index(
            "CC POS Print Job",
            ["status", "attempts", "creation"],
            POS_PRINT_QUEUE_INDEX,
        )
