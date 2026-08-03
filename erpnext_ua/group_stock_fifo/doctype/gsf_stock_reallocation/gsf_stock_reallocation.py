"""Controller for GSF Stock Reallocation (§9.14).

Server-owned like the allocation it serves: the reallocation service is the only
writer, because a hand-edited value here would claim a transfer the ledger never
made.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

WRITE_FLAG = "gsf_reallocation_service"


class GSFStockReallocation(Document):
    def validate(self) -> None:
        if not getattr(frappe.flags, WRITE_FLAG, False):
            frappe.throw(
                "GSF Stock Reallocation is written by the reallocation service and cannot "
                "be edited directly",
                title="MANUAL_REVIEW_REQUIRED",
            )

    def on_trash(self) -> None:
        if frappe.flags.in_uninstall:
            return
        frappe.throw(
            "GSF Stock Reallocation is audit evidence and cannot be deleted",
            title="MANUAL_REVIEW_REQUIRED",
        )
