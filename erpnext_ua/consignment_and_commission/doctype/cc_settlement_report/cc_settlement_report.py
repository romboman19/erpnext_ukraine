from decimal import Decimal

import frappe
from frappe.model.document import Document

WRITE_FLAG = "cc_settlement_service"
TEST_CLEANUP_FLAG = "cc_settlement_test_cleanup"
IMMUTABLE_FIELDS = (
    "idempotency_key",
    "request_fingerprint",
    "company",
    "partner_profile",
    "supplier",
    "contract",
    "relationship_model",
    "currency",
    "conversion_rate",
    "period_from",
    "period_to",
    "posting_date",
    "due_date",
    "total_partner_amount",
    "base_total_partner_amount",
)


class CCSettlementReport(Document):
    def validate(self) -> None:
        if not getattr(frappe.flags, WRITE_FLAG, False):
            frappe.throw("CC Settlement Report is server-owned and requires its controlled service")
        if self.relationship_model not in {"COMMISSION", "CONSIGNMENT"}:
            frappe.throw("CC Settlement Report requires COMMISSION or CONSIGNMENT")
        if not self.items:
            frappe.throw("CC Settlement Report requires at least one sold allocation")
        total = sum((Decimal(str(row.partner_amount or 0)) for row in self.items), Decimal("0"))
        base_total = sum(
            (Decimal(str(row.base_partner_amount or 0)) for row in self.items),
            Decimal("0"),
        )
        if total <= 0 or total != Decimal(str(self.total_partner_amount or 0)):
            frappe.throw("CC Settlement Report item total does not match its partner amount")
        if base_total <= 0 or base_total != Decimal(str(self.base_total_partner_amount or 0)):
            frappe.throw("CC Settlement Report item base total does not match its partner amount")
        paid = Decimal(str(self.paid_amount or 0))
        adjusted = Decimal(str(self.adjusted_amount or 0))
        base_adjusted = Decimal(str(self.base_adjusted_amount or 0))
        net = Decimal(str(self.net_partner_amount or 0))
        outstanding = Decimal(str(self.outstanding_amount or 0))
        credit = Decimal(str(self.partner_credit_amount or 0))
        if adjusted < 0 or adjusted > total or net != total - adjusted:
            frappe.throw("CC Settlement Report adjustment amounts do not reconcile")
        if base_adjusted < 0 or base_adjusted > base_total:
            frappe.throw("CC Settlement Report base adjustment is invalid")
        if paid < 0 or outstanding < 0 or credit < 0 or paid + outstanding != net + credit:
            frappe.throw("CC Settlement Report paid and outstanding amounts do not reconcile")
        if self.is_new():
            return
        persisted = frappe.db.get_value(
            "CC Settlement Report",
            self.name,
            list(IMMUTABLE_FIELDS),
            as_dict=True,
        )
        changed = [
            fieldname
            for fieldname in IMMUTABLE_FIELDS
            if str(self.get(fieldname) or "") != str(persisted.get(fieldname) or "")
        ] if persisted else []
        if changed:
            frappe.throw(f"CC Settlement Report is immutable: {', '.join(changed)}")

    def before_submit(self) -> None:
        from ...integrations.settlements import post_settlement_debt

        post_settlement_debt(self)

    def before_cancel(self) -> None:
        from ...integrations.settlements import cancel_settlement_debt

        cancel_settlement_debt(self)

    def on_cancel(self) -> None:
        from ...integrations.settlements import release_report_allocations

        release_report_allocations(self, cancelled=True)

    def on_trash(self) -> None:
        if self.docstatus == 0:
            from ...integrations.settlements import release_report_allocations

            release_report_allocations(self, cancelled=False)
            return
        if not frappe.in_test and not getattr(frappe.flags, TEST_CLEANUP_FLAG, False):
            frappe.throw("Submitted CC Settlement Report is immutable audit evidence")
