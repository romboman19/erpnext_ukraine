app_name = "erpnext_consignment_and_commission"
app_title = "ERPNext Consignment and Commission"
app_publisher = "HUNTER.rv"
app_description = "Commission and consignment trade for Frappe and ERPNext"
app_email = "it@hunter.rv.ua"
app_license = "MIT"

required_apps = ["erpnext", "erpnext_ua"]

# The Stock Entry hooks below are inert for native ERPNext transactions. They
# only protect and cascade-cancel entries explicitly linked to a CC Receipt.
fixtures = [
    {
        "dt": "Role",
        "filters": [
            [
                "name",
                "in",
                [
                    "Commission Trade Manager",
                    "Commission Trade User",
                    "Commission Trade Auditor",
                ],
            ]
        ],
    }
]

after_install = [
    "erpnext_consignment_and_commission.consignment_and_commission.setup.ownership_dimension.ensure_ownership_dimension"
]
after_migrate = [
    "erpnext_consignment_and_commission.consignment_and_commission.setup.ownership_dimension.ensure_ownership_dimension",
    "erpnext_consignment_and_commission.consignment_and_commission.setup.financial_backfill.backfill_financial_snapshots",
]

scheduler_events = {
    "all": [
        "erpnext_consignment_and_commission.consignment_and_commission.integrations.pos."
        "process_pending_print_jobs"
    ],
    "hourly_long": [
        "erpnext_consignment_and_commission.consignment_and_commission.integrations.reservations."
        "expire_due_allocations"
    ]
}

doc_events = {
    "Stock Entry": {
        "before_submit": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations.tracking."
            "validate_stock_entry_tracking_ownership"
        ),
        "before_cancel": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations.stock_entry."
            "guard_linked_receipt_cancellation"
        ),
        "on_cancel": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations.stock_entry."
            "allow_linked_receipt_cancellation"
        ),
    },
    "Purchase Invoice": {
        "before_submit": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations.tracking."
            "validate_purchase_invoice_tracking_ownership"
        ),
        "before_cancel": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations.purchase_invoice."
            "guard_linked_own_receipt_cancellation"
        ),
        "on_cancel": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations.purchase_invoice."
            "allow_linked_own_receipt_cancellation"
        ),
    },
    "Sales Invoice": {
        "before_submit": [
            (
                "erpnext_consignment_and_commission.consignment_and_commission.integrations."
                "sales_invoice.validate_managed_sales_invoice"
            ),
            (
                "erpnext_consignment_and_commission.consignment_and_commission.integrations."
                "tracking.validate_sales_invoice_tracking_ownership"
            ),
        ],
        "on_submit": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations."
            "sales_invoice.consume_sales_invoice_allocations"
        ),
        "before_cancel": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations."
            "sales_invoice.before_cancel_managed_sales_invoice"
        ),
        "on_cancel": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations."
            "sales_invoice.on_cancel_managed_sales_invoice"
        ),
        "on_trash": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations."
            "sales_invoice.release_draft_sales_invoice_allocations"
        ),
    },
    "Journal Entry": {
        "before_cancel": [
            (
                "erpnext_consignment_and_commission.consignment_and_commission.integrations."
                "sale_allocations.guard_recognition_cancellation"
            ),
            (
                "erpnext_consignment_and_commission.consignment_and_commission.integrations."
                "settlements.guard_settlement_debt_cancellation"
            ),
            (
                "erpnext_consignment_and_commission.consignment_and_commission.integrations."
                "settlement_adjustments.guard_adjustment_journal_cancellation"
            ),
        ]
    },
    "Payment Entry": {
        "validate": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations."
            "payments.validate_settlement_payment"
        ),
        "on_submit": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations."
            "payments.update_settlement_after_payment"
        ),
        "on_cancel": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations."
            "payments.update_settlement_after_payment"
        ),
    },
    "Batch": {
        "validate": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations.tracking."
            "validate_tracking_owner_immutability"
        ),
        "on_trash": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations.tracking."
            "guard_owned_tracking_deletion"
        ),
    },
    "Serial No": {
        "validate": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations.tracking."
            "validate_tracking_owner_immutability"
        ),
        "on_trash": (
            "erpnext_consignment_and_commission.consignment_and_commission.integrations.tracking."
            "guard_owned_tracking_deletion"
        ),
    },
}
