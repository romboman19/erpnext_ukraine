APP_NAME = "erpnext_ua"
MODULE_NAME = "Consignment and Commission"

REQUIRED_APPS = frozenset({"frappe", "erpnext", "erpnext_ua"})
OPTIONAL_APPS = frozenset({"ukrainian_integrations"})

DIAGNOSTIC_DOCTYPES = (
    "Sales Invoice",
    "Sales Invoice Item",
    "Stock Entry",
    "Stock Entry Detail",
    "Stock Ledger Entry",
    "Serial and Batch Bundle",
    "Inventory Dimension",
    "Journal Entry",
    "Payment Entry",
    "Warehouse",
    "UA Off Balance Entry",
)

FOUNDATION_DOCTYPES = (
    "CC Settings",
    "CC Location",
    "CC Partner Profile",
    "CC Contract",
    "CC Account Mapping",
)

STOCK_DOCTYPES = (
    "CC Receipt",
    "CC Receipt Item",
    "CC Own Receipt",
    "CC Own Receipt Item",
    "CC Stock Lot",
    "CC Price Version",
    "CC Allocation",
    "CC Allocation Slice",
    "CC Sale Allocation",
    "CC Sale Return Allocation",
    "CC Partner Return",
    "CC Ownership Conversion",
)

SETTLEMENT_DOCTYPES = (
    "CC Settlement Report",
    "CC Settlement Report Item",
    "CC Settlement Adjustment",
    "CC POS Checkout",
    "CC POS Route",
    "CC POS Route Item",
    "CC POS Route Payment",
    "CC POS Print Job",
)

FOUNDATION_ROLES = (
    "Commission Trade Manager",
    "Commission Trade User",
    "Commission Trade Auditor",
)

FOUNDATION_WORKSPACES = ("Commission Trade",)

DIAGNOSTIC_FIELDS = {
    "Account": ("ua_off_balance", "ua_chart_template", "ua_legal_source"),
    "Sales Invoice": (
        "is_pos",
        "update_stock",
        "is_return",
        "return_against",
        "cc_managed_sale",
        "cc_managed_return",
        "cc_pos_checkout",
        "cc_pos_route",
    ),
    "Sales Invoice Item": ("warehouse", "serial_and_batch_bundle"),
    "Payment Entry": ("references",),
}

STOCK_SCHEMA_FIELDS = {
    "CC Receipt Item": (
        "tracking_type",
        "batch_no",
        "serial_numbers",
        "accounting_unit_value",
        "accounting_amount",
        "off_balance_entry",
    ),
    "CC Own Receipt Item": ("tracking_type", "batch_no", "serial_numbers", "rate"),
    "CC Stock Lot": (
        "tracking_type",
        "batch_no",
        "serial_numbers",
        "ownership_conversion",
        "off_balance_account",
        "off_balance_unit_value",
        "off_balance_amount",
        "off_balance_currency",
        "off_balance_entry",
    ),
    "CC Price Version": ("stock_lot", "partner_rate", "valid_from", "valid_to"),
    "CC Allocation": ("idempotency_key", "status", "expires_at", "slices"),
    "CC Allocation Slice": ("stock_lot", "qty", "serial_no", "batch_no"),
    "CC Sale Allocation": (
        "sales_invoice",
        "allocation",
        "stock_lot",
        "partner_amount",
        "off_balance_amount",
        "off_balance_entry",
    ),
    "CC Sale Return Allocation": (
        "return_sales_invoice",
        "sale_allocation",
        "stock_lot",
        "returned_qty",
        "off_balance_amount",
        "off_balance_entry",
    ),
    "CC Partner Return": (
        "idempotency_key",
        "request_fingerprint",
        "source_lot",
        "qty",
        "serial_numbers",
        "stock_entry",
        "off_balance_amount",
        "off_balance_entry",
    ),
    "CC Ownership Conversion": (
        "idempotency_key",
        "request_fingerprint",
        "source_lot",
        "qty",
        "unit_cost",
        "source_issue",
        "own_receipt",
        "target_lot",
        "purchase_invoice",
        "off_balance_amount",
        "off_balance_entry",
    ),
    "CC Own Receipt": ("ownership_conversion",),
    "CC Settlement Report": (
        "supplier",
        "contract",
        "total_partner_amount",
        "outstanding_amount",
    ),
    "CC Settlement Adjustment": (
        "settlement_report",
        "return_sales_invoice",
        "amount",
        "journal_entry",
    ),
    "CC POS Checkout": (
        "idempotency_key",
        "request_fingerprint",
        "status",
        "payment_state",
    ),
    "CC POS Route": ("checkout", "group_id", "status", "sales_invoice", "print_job"),
    "CC POS Print Job": ("route", "status", "attempts", "idempotency_key"),
    "Stock Entry": ("cc_receipt", "cc_partner_return", "cc_ownership_conversion"),
    "Stock Entry Detail": ("cc_receipt_item", "cc_stock_lot", "to_cc_stock_lot"),
    "Stock Ledger Entry": ("cc_stock_lot",),
    "Purchase Invoice": ("cc_own_receipt", "cc_ownership_conversion"),
    "Purchase Invoice Item": ("cc_own_receipt_item", "cc_stock_lot"),
    "Batch": ("cc_stock_lot",),
    "Serial No": ("cc_stock_lot",),
}
