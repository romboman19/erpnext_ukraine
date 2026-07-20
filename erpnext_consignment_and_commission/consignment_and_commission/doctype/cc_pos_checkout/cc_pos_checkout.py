from decimal import Decimal

import frappe
from frappe.model.document import Document

WRITE_FLAG = "cc_pos_checkout_service"
TEST_CLEANUP_FLAG = "cc_pos_checkout_test_cleanup"


class CCPOSCheckout(Document):
    def validate(self) -> None:
        if not getattr(frappe.flags, WRITE_FLAG, False):
            frappe.throw("CC POS Checkout is server-owned")
        total = Decimal(str(self.total_amount or 0))
        payment = Decimal(str(self.payment_total or 0))
        rate = Decimal(str(self.conversion_rate or 0))
        if total <= 0 or payment != total or rate <= 0:
            frappe.throw("CC POS Checkout totals and conversion rate do not reconcile")
        if int(self.routes_count or 0) <= 0:
            frappe.throw("CC POS Checkout requires at least one route")

    def on_trash(self) -> None:
        if not frappe.in_test and not getattr(frappe.flags, TEST_CLEANUP_FLAG, False):
            frappe.throw("CC POS Checkout is immutable operational evidence")
