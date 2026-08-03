from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

ROLES = ("UA Loyalty User", "UA Loyalty Manager", "UA Loyalty Auditor", "UA Loyalty Administrator")


def ensure_loyalty_setup():
    for role in ROLES:
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)
    if not frappe.db.exists("DocType", "UA Loyalty Settings"):
        return
    create_custom_fields(_custom_fields(), update=True)
    _ensure_indexes()
    _ensure_navigation()
    frappe.clear_cache()
    frappe.db.commit()


def _custom_fields() -> dict:
    return {
        "Sales Invoice": [
            {
                "fieldname": "ua_loyalty_section",
                "label": "UA Loyalty",
                "fieldtype": "Section Break",
                "insert_after": "ua_pos_shift",
            },
            {
                "fieldname": "ua_loyalty_scope",
                "label": "Loyalty Scope",
                "fieldtype": "Link",
                "options": "UA Loyalty Scope",
                "read_only": 1,
                "insert_after": "ua_loyalty_section",
            },
            {
                "fieldname": "ua_loyalty_location",
                "label": "Loyalty Location",
                "fieldtype": "Link",
                "options": "UA Loyalty Location",
                "read_only": 1,
                "insert_after": "ua_loyalty_scope",
            },
            {
                "fieldname": "ua_loyalty_program",
                "label": "Loyalty Program",
                "fieldtype": "Link",
                "options": "UA Loyalty Program",
                "read_only": 1,
                "insert_after": "ua_loyalty_location",
            },
            {
                "fieldname": "ua_loyalty_program_version",
                "label": "Program Version",
                "fieldtype": "Int",
                "read_only": 1,
                "insert_after": "ua_loyalty_program",
            },
            {
                "fieldname": "ua_loyalty_account",
                "label": "Loyalty Account",
                "fieldtype": "Link",
                "options": "UA Loyalty Account",
                "read_only": 1,
                "insert_after": "ua_loyalty_program_version",
            },
            {
                "fieldname": "ua_loyalty_snapshot_hash",
                "label": "Snapshot Hash",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "ua_loyalty_account",
            },
            {
                "fieldname": "ua_loyalty_quote_hash",
                "label": "Quote Hash",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "ua_loyalty_snapshot_hash",
            },
            {
                "fieldname": "ua_loyalty_reservation",
                "label": "Reservation",
                "fieldtype": "Link",
                "options": "UA Loyalty Reservation",
                "read_only": 1,
                "insert_after": "ua_loyalty_quote_hash",
            },
            {
                "fieldname": "ua_loyalty_redeemed_amount",
                "label": "Redeemed Bonus",
                "fieldtype": "Currency",
                "read_only": 1,
                "insert_after": "ua_loyalty_reservation",
            },
            {
                "fieldname": "ua_loyalty_earned_active",
                "label": "Earned Active",
                "fieldtype": "Currency",
                "read_only": 1,
                "insert_after": "ua_loyalty_redeemed_amount",
            },
            {
                "fieldname": "ua_loyalty_earned_pending",
                "label": "Earned Pending",
                "fieldtype": "Currency",
                "read_only": 1,
                "insert_after": "ua_loyalty_earned_active",
            },
            {
                "fieldname": "ua_loyalty_metric_delta",
                "label": "Metric Delta",
                "fieldtype": "Currency",
                "read_only": 1,
                "insert_after": "ua_loyalty_earned_pending",
            },
            {
                "fieldname": "ua_loyalty_posted",
                "label": "Loyalty Posted",
                "fieldtype": "Check",
                "read_only": 1,
                "insert_after": "ua_loyalty_metric_delta",
            },
            {
                "fieldname": "ua_loyalty_posting_key",
                "label": "Posting Key",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "ua_loyalty_posted",
            },
            {
                "fieldname": "ua_loyalty_snapshot_json",
                "label": "Loyalty Snapshot",
                "fieldtype": "Long Text",
                "read_only": 1,
                "insert_after": "ua_loyalty_posting_key",
            },
        ],
        "Sales Invoice Item": [
            {
                "fieldname": "ua_pos_order_item",
                "label": "POS Order Item",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "item_code",
            },
            {
                "fieldname": "ua_loyalty_non_loyalty_discount",
                "label": "Other Discount",
                "fieldtype": "Currency",
                "read_only": 1,
                "insert_after": "discount_amount",
            },
            {
                "fieldname": "ua_loyalty_redeemed_amount",
                "label": "Loyalty Discount",
                "fieldtype": "Currency",
                "read_only": 1,
                "insert_after": "ua_loyalty_non_loyalty_discount",
            },
            {
                "fieldname": "ua_loyalty_earn_base",
                "label": "Loyalty Earn Base",
                "fieldtype": "Currency",
                "read_only": 1,
                "insert_after": "ua_loyalty_redeemed_amount",
            },
            {
                "fieldname": "ua_loyalty_amount_before",
                "label": "Amount Before Loyalty",
                "fieldtype": "Currency",
                "read_only": 1,
                "insert_after": "ua_loyalty_earn_base",
            },
            {
                "fieldname": "ua_loyalty_earned_amount",
                "label": "Earned Bonus",
                "fieldtype": "Currency",
                "read_only": 1,
                "insert_after": "ua_loyalty_amount_before",
            },
            {
                "fieldname": "ua_loyalty_metric_delta",
                "label": "Metric Delta",
                "fieldtype": "Currency",
                "read_only": 1,
                "insert_after": "ua_loyalty_earned_amount",
            },
            {
                "fieldname": "ua_loyalty_earn_percent",
                "label": "Earn Percent",
                "fieldtype": "Percent",
                "read_only": 1,
                "insert_after": "ua_loyalty_metric_delta",
            },
            {
                "fieldname": "ua_loyalty_metric_eligible",
                "label": "Metric Eligible",
                "fieldtype": "Check",
                "read_only": 1,
                "insert_after": "ua_loyalty_earn_percent",
            },
            {
                "fieldname": "ua_loyalty_eligibility_reason",
                "label": "Eligibility Reason",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "ua_loyalty_metric_eligible",
            },
            {
                "fieldname": "ua_loyalty_rule_snapshot",
                "label": "Loyalty Rule Snapshot",
                "fieldtype": "Long Text",
                "read_only": 1,
                "insert_after": "ua_loyalty_eligibility_reason",
            },
            {
                "fieldname": "ua_loyalty_original_invoice_item",
                "label": "Original Invoice Item",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "ua_loyalty_rule_snapshot",
            },
            {
                "fieldname": "ua_loyalty_original_order_item",
                "label": "Original POS Item",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "ua_loyalty_original_invoice_item",
            },
            {
                "fieldname": "ua_loyalty_allocation_hash",
                "label": "Allocation Hash",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "ua_loyalty_original_order_item",
            },
        ],
        "PRRO Receipt": [
            {
                "fieldname": "loyalty_redeemed_amount",
                "label": "Loyalty Discount",
                "fieldtype": "Currency",
                "read_only": 1,
                "insert_after": "sales_invoice",
            },
            {
                "fieldname": "loyalty_scope",
                "label": "Loyalty Scope",
                "fieldtype": "Link",
                "options": "UA Loyalty Scope",
                "read_only": 1,
                "insert_after": "loyalty_redeemed_amount",
            },
            {
                "fieldname": "loyalty_snapshot_hash",
                "label": "Loyalty Snapshot Hash",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "loyalty_scope",
            },
        ],
    }


def _ensure_indexes():
    indexes = (
        ("UA Loyalty Account", ["customer", "scope"], "ua_loyalty_account_customer_scope"),
        ("UA Loyalty Ledger Entry", ["account", "posting_datetime", "name"], "ua_loyalty_ledger_statement"),
        ("UA Loyalty Allocation", ["source_name", "source_row_name"], "ua_loyalty_allocation_source"),
        ("UA Loyalty Reservation", ["account", "status", "expires_at"], "ua_loyalty_reservation_account_state"),
    )
    for doctype, fields, name in indexes:
        if frappe.db.exists("DocType", doctype):
            frappe.db.add_index(doctype, fields, name)
    unique_indexes = (
        ("UA Loyalty Account", ["customer", "scope"], "uniq_ua_loyalty_account_customer_scope"),
        ("UA Loyalty Card", ["barcode"], "uniq_ua_loyalty_card_barcode"),
        ("UA Loyalty Ledger Entry", ["idempotency_key"], "uniq_ua_loyalty_ledger_idempotency"),
        ("UA Loyalty Metric Entry", ["idempotency_key"], "uniq_ua_loyalty_metric_idempotency"),
        ("UA Loyalty Allocation", ["idempotency_key"], "uniq_ua_loyalty_allocation_idempotency"),
        ("UA Loyalty Reservation", ["idempotency_key"], "uniq_ua_loyalty_reservation_idempotency"),
        ("UA Loyalty Rule Snapshot", ["snapshot_hash"], "uniq_ua_loyalty_snapshot_hash"),
        (
            "UA Loyalty Expiry Obligation",
            ["idempotency_key"],
            "uniq_ua_loyalty_expiry_idempotency",
        ),
        ("UA Loyalty Adjustment", ["idempotency_key"], "uniq_ua_loyalty_adjustment_idempotency"),
        (
            "UA Loyalty Account Change Log",
            ["idempotency_key"],
            "uniq_ua_loyalty_change_log_idempotency",
        ),
    )
    for doctype, fields, name in unique_indexes:
        if frappe.db.exists("DocType", doctype):
            frappe.db.add_unique(doctype, fields, name)


def _ensure_navigation():
    if not frappe.db.table_exists("Workspace"):
        return
    from frappe.modules.import_file import import_file_by_path

    loyalty_paths = (
        ("workspace", "ua_loyalty", "ua_loyalty.json"),
        ("report", "ua_loyalty_movements", "ua_loyalty_movements.json"),
        ("report", "ua_loyalty_negative_balances", "ua_loyalty_negative_balances.json"),
        ("report", "ua_loyalty_reservations", "ua_loyalty_reservations.json"),
        ("report", "ua_loyalty_reconciliation", "ua_loyalty_reconciliation.json"),
    )
    for parts in loyalty_paths:
        import_file_by_path(frappe.get_app_path("erpnext_ua", "ua_loyalty", *parts), force=True)
    import_file_by_path(
        frappe.get_app_path("erpnext_ua", "workspace_sidebar", "ua_loyalty.json"), force=True
    )
