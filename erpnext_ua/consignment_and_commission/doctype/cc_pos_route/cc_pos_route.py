from decimal import Decimal

import frappe
from frappe.model.document import Document

WRITE_FLAG = "cc_pos_route_service"
TEST_CLEANUP_FLAG = "cc_pos_route_test_cleanup"


class CCPOSRoute(Document):
    def validate(self) -> None:
        if not getattr(frappe.flags, WRITE_FLAG, False):
            frappe.throw("CC POS Route is server-owned")
        if self.fiscal_route not in {"FISCAL", "NON_FISCAL"} or not self.items:
            frappe.throw("CC POS Route is incomplete")
        total = sum(
            (Decimal(str(row.amount or 0)) for row in self.items),
            Decimal("0"),
        )
        payments = sum(
            (Decimal(str(row.amount or 0)) for row in self.payments),
            Decimal("0"),
        )
        if total <= 0 or total != Decimal(str(self.total_amount or 0)) or payments != total:
            frappe.throw("CC POS Route item and payment totals do not reconcile")

    def on_trash(self) -> None:
        if not frappe.in_test and not getattr(frappe.flags, TEST_CLEANUP_FLAG, False):
            frappe.throw("CC POS Route is immutable operational evidence")
