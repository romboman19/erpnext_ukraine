"""Controller for GSF Layer Movement (§9.11).

An immutable audit event. §34.3 forbids editing a movement after the operation
finishes — corrections are made by writing a reversal, not by rewriting
history. Withholding `write` in the DocType's permissions only stops the desk;
the guard here is what stops server-side code, which is where movements are
actually written from.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from erpnext_ua.group_stock_fifo.services.domain import (
    GSFError,
    MovementFacts,
    validate_movement,
)


class GSFLayerMovement(Document):
    def validate(self) -> None:
        if not self.is_new():
            frappe.throw(
                "A layer movement is immutable; correct it with a reversal instead",
                title="MANUAL_REVIEW_REQUIRED",
            )
        try:
            validate_movement(
                MovementFacts(
                    movement_type=self.movement_type,
                    qty=self.qty,
                    idempotency_key=self.idempotency_key,
                    is_reversal=bool(self.is_reversal),
                    reversal_of=self.reversal_of,
                )
            )
        except GSFError as error:
            frappe.throw(str(error), title=error.code)

    def on_trash(self) -> None:
        if frappe.flags.in_uninstall:
            return
        frappe.throw(
            "A layer movement cannot be deleted; write a reversal instead",
            title="MANUAL_REVIEW_REQUIRED",
        )
