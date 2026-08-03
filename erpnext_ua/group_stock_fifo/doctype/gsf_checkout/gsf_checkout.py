"""Controller for GSF Checkout (§9.16).

Server-owned, with one deliberate exception: the manual-review fields stay
writable. §35's whole point is that an operator has to be able to take a stuck
saga over, and a document they cannot annotate is one they cannot take over.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from erpnext_ua.group_stock_fifo.services.checkout_states import validate_transition
from erpnext_ua.group_stock_fifo.services.domain import GSFError

WRITE_FLAG = "gsf_checkout_service"

#: Editable by an operator even outside the service, because taking over a
#: stuck checkout is the one thing they are supposed to do by hand.
OPERATOR_FIELDS = frozenset({"manual_review_reason", "payment_state"})


class GSFCheckout(Document):
    def validate(self) -> None:
        if self.is_new():
            self._assert_service_write()
            return

        before = self.get_doc_before_save()
        if not before:
            return
        if before.status != self.status:
            self._assert_service_write()
            try:
                validate_transition(before.status, self.status)
            except GSFError as error:
                frappe.throw(str(error), title=error.code)
        elif self._changed_beyond_operator_fields(before):
            self._assert_service_write()

    def _assert_service_write(self) -> None:
        if not getattr(frappe.flags, WRITE_FLAG, False):
            frappe.throw(
                "GSF Checkout is driven by the checkout service; only the review notes "
                "may be edited by hand",
                title="MANUAL_REVIEW_REQUIRED",
            )

    def _changed_beyond_operator_fields(self, before) -> bool:
        return any(
            str(before.get(field.fieldname) or "") != str(self.get(field.fieldname) or "")
            for field in self.meta.fields
            if field.fieldname not in OPERATOR_FIELDS and field.fieldtype not in ("Table",)
        )

    def on_trash(self) -> None:
        if frappe.flags.in_uninstall:
            return
        frappe.throw(
            "GSF Checkout is audit evidence and cannot be deleted",
            title="MANUAL_REVIEW_REQUIRED",
        )
