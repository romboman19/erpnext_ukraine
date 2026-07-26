from decimal import Decimal

import frappe
from frappe.model.document import Document

WRITE_FLAG = "cc_settlement_adjustment_service"
TEST_CLEANUP_FLAG = "cc_settlement_adjustment_test_cleanup"
IMMUTABLE_FIELDS = (
    "adjustment_type",
    "settlement_report",
    "return_sales_invoice",
    "posting_date",
    "company",
    "supplier",
    "relationship_model",
    "currency",
    "amount",
    "base_amount",
    "conversion_rate",
    "applied_to_outstanding_amount",
    "credit_due_amount",
)
NUMERIC_IMMUTABLE_FIELDS = {
    "amount",
    "base_amount",
    "conversion_rate",
    "applied_to_outstanding_amount",
    "credit_due_amount",
}


class CCSettlementAdjustment(Document):
    def validate(self) -> None:
        if not getattr(frappe.flags, WRITE_FLAG, False):
            frappe.throw("CC Settlement Adjustment is server-owned")
        if self.adjustment_type != "RETURN_REVERSAL":
            frappe.throw("Unsupported CC Settlement Adjustment type")
        if self.relationship_model not in {"COMMISSION", "CONSIGNMENT"}:
            frappe.throw("CC Settlement Adjustment requires a third-party model")
        amount = Decimal(str(self.amount or 0))
        base_amount = Decimal(str(self.base_amount or 0))
        applied = Decimal(str(self.applied_to_outstanding_amount or 0))
        credit = Decimal(str(self.credit_due_amount or 0))
        rate = Decimal(str(self.conversion_rate or 0))
        if amount <= 0 or base_amount <= 0 or rate <= 0:
            frappe.throw("CC Settlement Adjustment amounts and rate must be positive")
        if applied < 0 or credit < 0 or applied + credit != amount:
            frappe.throw("CC Settlement Adjustment application does not reconcile")
        if self.is_new():
            return
        persisted = frappe.db.get_value(
            "CC Settlement Adjustment",
            self.name,
            list(IMMUTABLE_FIELDS),
            as_dict=True,
        )
        changed = []
        for fieldname in IMMUTABLE_FIELDS:
            current = self.get(fieldname)
            previous = persisted.get(fieldname) if persisted else None
            equal = (
                Decimal(str(current or 0)) == Decimal(str(previous or 0))
                if fieldname in NUMERIC_IMMUTABLE_FIELDS
                else str(current or "") == str(previous or "")
            )
            if not equal:
                changed.append(fieldname)
        if changed:
            frappe.throw(f"CC Settlement Adjustment is immutable: {', '.join(changed)}")

    def before_submit(self) -> None:
        from ...integrations.settlement_adjustments import post_adjustment_journal

        post_adjustment_journal(self)

    def on_submit(self) -> None:
        from ...integrations.settlements import refresh_settlement_lifecycle

        self.status = "POSTED"
        refresh_settlement_lifecycle(frappe, self.settlement_report)

    def before_cancel(self) -> None:
        from ...integrations.settlement_adjustments import cancel_adjustment_journal

        cancel_adjustment_journal(self)

    def on_cancel(self) -> None:
        from ...integrations.settlements import refresh_settlement_lifecycle

        self.status = "CANCELLED"
        refresh_settlement_lifecycle(frappe, self.settlement_report)

    def on_trash(self) -> None:
        if not frappe.in_test and not getattr(frappe.flags, TEST_CLEANUP_FLAG, False):
            frappe.throw("CC Settlement Adjustment is immutable audit evidence")
