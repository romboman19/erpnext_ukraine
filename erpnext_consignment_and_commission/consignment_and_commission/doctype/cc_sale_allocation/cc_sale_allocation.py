from decimal import Decimal

import frappe
from frappe.model.document import Document

from ...services.allocation import SOURCE_METHOD_RELATIONSHIP_MODEL

WRITE_FLAG = "cc_sale_allocation_service"
TEST_CLEANUP_FLAG = "cc_sale_allocation_test_cleanup"
IMMUTABLE_FIELDS = (
    "sales_invoice",
    "sales_invoice_item",
    "allocation",
    "allocation_slice",
    "posting_date",
    "posting_datetime",
    "company",
    "customer",
    "item_code",
    "stock_lot",
    "warehouse",
    "source_method",
    "relationship_model",
    "serial_no",
    "batch_no",
    "partner_profile",
    "contract",
    "supplier",
    "currency",
    "conversion_rate",
    "sold_qty",
    "net_rate",
    "net_amount",
    "commission_rate",
    "commission_amount",
    "price_version",
    "partner_unit_rate",
    "partner_amount",
    "retained_amount",
    "base_net_amount",
    "base_commission_amount",
    "base_partner_amount",
    "base_retained_amount",
)


class CCSaleAllocation(Document):
    def validate(self) -> None:
        if not getattr(frappe.flags, WRITE_FLAG, False):
            frappe.throw("CC Sale Allocation is server-owned and cannot be edited directly")
        expected_model = SOURCE_METHOD_RELATIONSHIP_MODEL.get(self.source_method)
        if expected_model != self.relationship_model:
            frappe.throw("CC Sale Allocation source method and relationship model disagree")
        qty = Decimal(str(self.sold_qty or 0))
        net = Decimal(str(self.net_amount or 0))
        partner = Decimal(str(self.partner_amount or 0))
        retained = Decimal(str(self.retained_amount or 0))
        commission = Decimal(str(self.commission_amount or 0))
        base_net = Decimal(str(self.base_net_amount or 0))
        base_commission = Decimal(str(self.base_commission_amount or 0))
        base_partner = Decimal(str(self.base_partner_amount or 0))
        base_retained = Decimal(str(self.base_retained_amount or 0))
        if qty <= 0 or net <= 0:
            frappe.throw("CC Sale Allocation quantity and net amount must be positive")
        if net != partner + retained:
            frappe.throw("CC Sale Allocation partner plus retained amount must equal net amount")
        if base_net <= 0 or base_net != base_partner + base_retained:
            frappe.throw("CC Sale Allocation base financial snapshot is not balanced")
        if self.relationship_model == "OWN":
            if any(
                (
                    partner,
                    commission,
                    base_partner,
                    base_commission,
                    Decimal(str(self.commission_rate or 0)),
                )
            ):
                frappe.throw("OWN sale allocation cannot create partner or commission debt")
        elif self.relationship_model == "COMMISSION":
            if (
                retained != commission
                or base_retained != base_commission
                or not self.contract
                or not self.supplier
            ):
                frappe.throw("Commission sale allocation financial snapshot is incomplete")
        elif self.relationship_model == "CONSIGNMENT":
            if base_commission or not self.price_version or not self.contract or not self.supplier:
                frappe.throw("Consignment sale allocation requires its effective price version")

        if self.is_new():
            return
        persisted = frappe.db.get_value(
            "CC Sale Allocation",
            self.name,
            ["status", "returned_qty", "settled_partner_amount", *IMMUTABLE_FIELDS],
            as_dict=True,
        )
        if not persisted:
            return
        changed = [
            fieldname
            for fieldname in IMMUTABLE_FIELDS
            if str(self.get(fieldname) or "") != str(persisted.get(fieldname) or "")
        ]
        if changed:
            frappe.throw(f"CC Sale Allocation snapshot is immutable: {', '.join(changed)}")
        returned = Decimal(str(self.returned_qty or 0))
        settled = Decimal(str(self.settled_partner_amount or 0))
        if returned < 0 or returned > qty:
            frappe.throw("CC Sale Allocation returned quantity is outside the sold quantity")
        if settled < 0 or settled > partner:
            frappe.throw("CC Sale Allocation settled amount is outside the partner amount")

    def on_trash(self) -> None:
        if not frappe.in_test and not getattr(frappe.flags, TEST_CLEANUP_FLAG, False):
            frappe.throw("CC Sale Allocation is immutable audit evidence and cannot be deleted")
