from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

ROLES = (
    "Gift Certificate Cashier",
    "Gift Certificate Senior Cashier",
    "Gift Certificate Manager",
    "Gift Certificate Accountant",
    "Gift Certificate Compliance Officer",
    "Gift Certificate Auditor",
    "Gift Certificate Administrator",
)

DOCTYPE_FOLDERS = (
    "pos_gift_certificate_issue_row",
    "ua_gift_certificate_eligibility_rule",
    "ua_gift_certificate_network_location",
    "ua_gift_certificate_network_entity",
    "ua_gift_certificate_sale_item",
    "ua_gift_certificate_batch",
    "ua_gift_certificate_adjustment",
    "ua_gift_certificate_settlement_batch",
    "ua_gift_certificate_policy_snapshot",
    "ua_gift_certificate_import_batch",
    "ua_gift_certificate_print_grant",
    "ua_gift_certificate_settings",
    "ua_gift_certificate_accounting_profile",
    "ua_gift_certificate_compliance_profile",
    "ua_gift_certificate_settlement_profile",
    "ua_gift_certificate_network",
    "ua_gift_certificate_program",
    "ua_gift_certificate",
    "ua_gift_certificate_ledger_entry",
    "ua_gift_certificate_reservation",
    "ua_gift_certificate_redemption_allocation",
    "ua_gift_certificate_return_allocation",
    "ua_gift_certificate_sale",
    "ua_gift_certificate_settlement_entry",
    "ua_gift_certificate_replacement",
    "ua_gift_certificate_tax_event",
    "ua_gift_certificate_configuration_audit",
)


def ensure_gift_certificate_setup():
    _ensure_doctypes_on_first_module_migrate()
    for role in ROLES:
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)
    if not frappe.db.exists("DocType", "UA Gift Certificate Settings"):
        return
    create_custom_fields(_custom_fields(), update=True)
    ensure_indexes()
    frappe.db.commit()


def _ensure_doctypes_on_first_module_migrate():
    """A Module Def added to an installed app is discovered after its first sync.

    Reloading only on that first migration avoids requiring operators to run
    ``bench migrate`` twice when upgrading an existing ERPNext UA site.
    """
    if frappe.db.exists("DocType", "UA Gift Certificate Settings"):
        return
    for folder in DOCTYPE_FOLDERS:
        frappe.reload_doc("ua_gift_certificates", "doctype", folder, force=True)


def _custom_fields():
    return {
        "UA Gift Certificate Program": [
            {
                "fieldname": "print_token_once",
                "label": "Print Token Once",
                "fieldtype": "Check",
                "default": "1",
                "insert_after": "expiry_accounting_policy",
            },
            {
                "fieldname": "reprint_requires_approval",
                "label": "Reprint Requires Approval",
                "fieldtype": "Check",
                "default": "1",
                "insert_after": "print_token_once",
            },
        ],
        "UA Gift Certificate": [
            {
                "fieldname": "batch",
                "label": "Batch",
                "fieldtype": "Link",
                "options": "UA Gift Certificate Batch",
                "read_only": 1,
                "insert_after": "program_version",
            },
            {
                "fieldname": "sale_sales_invoice",
                "label": "Sale Sales Invoice",
                "fieldtype": "Link",
                "options": "Sales Invoice",
                "read_only": 1,
                "insert_after": "certificate_sale",
            },
        ],
        "Sales Invoice": [
            {
                "fieldname": "ua_gift_certificate_section",
                "label": "UA Gift Certificates",
                "fieldtype": "Section Break",
                "insert_after": "ua_pos_shift",
            },
            {
                "fieldname": "ua_gift_certificate_context",
                "label": "Gift Certificate Context",
                "fieldtype": "Check",
                "default": "0",
                "read_only": 1,
                "insert_after": "ua_gift_certificate_section",
            },
            {
                "fieldname": "ua_gift_certificate_sale",
                "label": "Gift Certificate Sale",
                "fieldtype": "Link",
                "options": "UA Gift Certificate Sale",
                "read_only": 1,
                "insert_after": "ua_gift_certificate_context",
            },
            {
                "fieldname": "ua_gift_certificate_redemption_total",
                "label": "Gift Certificate Redemption",
                "fieldtype": "Currency",
                "default": "0",
                "read_only": 1,
                "insert_after": "ua_gift_certificate_sale",
            },
            {
                "fieldname": "ua_gift_certificate_paid_component",
                "label": "Gift Certificate Paid Component",
                "fieldtype": "Currency",
                "default": "0",
                "read_only": 1,
                "insert_after": "ua_gift_certificate_redemption_total",
            },
            {
                "fieldname": "ua_gift_certificate_promotional_component",
                "label": "Gift Certificate Promotional Component",
                "fieldtype": "Currency",
                "default": "0",
                "read_only": 1,
                "insert_after": "ua_gift_certificate_paid_component",
            },
            {
                "fieldname": "ua_gift_certificate_settlement_total",
                "label": "Gift Certificate Settlement",
                "fieldtype": "Currency",
                "default": "0",
                "read_only": 1,
                "insert_after": "ua_gift_certificate_promotional_component",
            },
            {
                "fieldname": "ua_gift_certificate_snapshot",
                "label": "Gift Certificate Snapshot",
                "fieldtype": "Long Text",
                "read_only": 1,
                "insert_after": "ua_gift_certificate_settlement_total",
            },
            {
                "fieldname": "ua_gift_certificate_posting_status",
                "label": "Gift Certificate Posting",
                "fieldtype": "Select",
                "options": "\nPending\nPosted\nReversed\nManual Review",
                "read_only": 1,
                "insert_after": "ua_gift_certificate_snapshot",
            },
            {
                "fieldname": "ua_gift_certificate_recovery_note",
                "label": "Gift Certificate Recovery Note",
                "fieldtype": "Small Text",
                "read_only": 1,
                "insert_after": "ua_gift_certificate_posting_status",
            },
        ],
        "Sales Invoice Item": [
            {
                "fieldname": "ua_gift_certificate_amount",
                "label": "Gift Certificate Amount",
                "fieldtype": "Currency",
                "default": "0",
                "read_only": 1,
            },
            {
                "fieldname": "ua_gift_certificate_paid_component",
                "label": "Gift Certificate Paid",
                "fieldtype": "Currency",
                "default": "0",
                "read_only": 1,
            },
            {
                "fieldname": "ua_gift_certificate_promotional_component",
                "label": "Gift Certificate Promotional",
                "fieldtype": "Currency",
                "default": "0",
                "read_only": 1,
            },
            {
                "fieldname": "ua_gift_certificate_allocation_count",
                "label": "Gift Certificate Allocations",
                "fieldtype": "Int",
                "default": "0",
                "read_only": 1,
            },
            {
                "fieldname": "ua_gift_certificate_eligible",
                "label": "Gift Certificate Eligible",
                "fieldtype": "Check",
                "default": "0",
                "read_only": 1,
            },
            {
                "fieldname": "ua_gift_certificate_policy_reason",
                "label": "Gift Certificate Policy Reason",
                "fieldtype": "Data",
                "read_only": 1,
            },
        ],
        "Mode of Payment": [
            {
                "fieldname": "ua_gift_certificate_component",
                "label": "Gift Certificate Component",
                "fieldtype": "Select",
                "options": "None\nPaid Liability\nPromotional\nSettlement Receivable\nSettlement Payable",
                "default": "None",
                "insert_after": "ua_pos_kind",
            },
            {
                "fieldname": "ua_gift_certificate_network",
                "label": "Gift Certificate Network",
                "fieldtype": "Link",
                "options": "UA Gift Certificate Network",
                "insert_after": "ua_gift_certificate_component",
            },
            {
                "fieldname": "ua_gift_certificate_internal_only",
                "label": "Gift Certificate Internal Only",
                "fieldtype": "Check",
                "default": "0",
                "insert_after": "ua_gift_certificate_network",
            },
            {
                "fieldname": "ua_gift_certificate_prro_name",
                "label": "Gift Certificate PRRO Name",
                "fieldtype": "Data",
                "insert_after": "ua_gift_certificate_internal_only",
            },
            {
                "fieldname": "ua_gift_certificate_accounting_profile",
                "label": "Gift Certificate Accounting Profile",
                "fieldtype": "Link",
                "options": "UA Gift Certificate Accounting Profile",
                "insert_after": "ua_gift_certificate_prro_name",
            },
        ],
        "PRRO Receipt": [
            {
                "fieldname": "gift_certificate_sale",
                "label": "Gift Certificate Sale",
                "fieldtype": "Link",
                "options": "UA Gift Certificate Sale",
                "read_only": 1,
            },
            {
                "fieldname": "gift_certificate_payment_total",
                "label": "Gift Certificate Payment",
                "fieldtype": "Currency",
                "default": "0",
                "read_only": 1,
            },
            {
                "fieldname": "gift_certificate_payment_rows_json",
                "label": "Gift Certificate Payment Rows",
                "fieldtype": "Long Text",
                "read_only": 1,
            },
            {
                "fieldname": "gift_certificate_tax_event_status",
                "label": "Gift Certificate Tax Event",
                "fieldtype": "Data",
                "read_only": 1,
            },
            {
                "fieldname": "gift_certificate_compliance_profile_version",
                "label": "Gift Certificate Compliance Profile",
                "fieldtype": "Data",
                "read_only": 1,
            },
        ],
    }


def ensure_indexes():
    indexes = {
        "UA Gift Certificate": [
            ("network", "status", "valid_until"),
            ("issuer_company", "issuer_fop_profile", "status"),
        ],
        "UA Gift Certificate Ledger Entry": [("certificate", "posting_datetime")],
        "UA Gift Certificate Reservation": [("certificate", "status", "expires_at")],
        "UA Gift Certificate Redemption Allocation": [
            ("sales_invoice", "sales_invoice_item"),
            ("certificate", "pos_order"),
        ],
        "UA Gift Certificate Settlement Entry": [("issuer_company", "redeemer_company", "status", "posting_date")],
        "UA Gift Certificate Print Grant": [("certificate", "creation")],
    }
    for doctype, fields_list in indexes.items():
        if not frappe.db.table_exists(doctype):
            continue
        for fields in fields_list:
            frappe.db.add_index(doctype, fields)
