from decimal import Decimal

import frappe
from frappe.model.document import Document

from ...services.allocation import SOURCE_METHOD_RELATIONSHIP_MODEL

WRITE_FLAG = "cc_sale_return_service"
TEST_CLEANUP_FLAG = "cc_sale_return_test_cleanup"


class CCSaleReturnAllocation(Document):
    def validate(self) -> None:
        if not getattr(frappe.flags, WRITE_FLAG, False):
            frappe.throw("CC Sale Return Allocation is server-owned")
        expected_model = SOURCE_METHOD_RELATIONSHIP_MODEL.get(self.source_method)
        if expected_model != self.relationship_model:
            frappe.throw("CC Sale Return Allocation source method and model disagree")
        qty = Decimal(str(self.returned_qty or 0))
        gross = Decimal(str(self.net_amount or 0))
        commission = Decimal(str(self.commission_amount or 0))
        partner = Decimal(str(self.partner_amount or 0))
        retained = Decimal(str(self.retained_amount or 0))
        base_gross = Decimal(str(self.base_net_amount or 0))
        base_commission = Decimal(str(self.base_commission_amount or 0))
        base_partner = Decimal(str(self.base_partner_amount or 0))
        base_retained = Decimal(str(self.base_retained_amount or 0))
        if qty <= 0 or gross <= 0:
            frappe.throw("CC Sale Return Allocation quantity and net amount must be positive")
        if gross != partner + retained:
            frappe.throw("CC Sale Return Allocation partner plus retained must equal net amount")
        if base_gross <= 0 or base_gross != base_partner + base_retained:
            frappe.throw("CC Sale Return Allocation base financial snapshot is not balanced")
        if self.relationship_model == "OWN" and any(
            (partner, commission, base_partner, base_commission)
        ):
            frappe.throw("OWN return cannot reverse commission or partner debt")
        if self.relationship_model == "COMMISSION" and (
            commission != retained or base_commission != base_retained
        ):
            frappe.throw("Commission return retained amount must equal commission reversal")
        if self.relationship_model == "CONSIGNMENT" and (commission or base_commission):
            frappe.throw("Consignment return cannot reverse commission income")

    def on_trash(self) -> None:
        if not frappe.in_test and not getattr(frappe.flags, TEST_CLEANUP_FLAG, False):
            frappe.throw("CC Sale Return Allocation is immutable audit evidence")
