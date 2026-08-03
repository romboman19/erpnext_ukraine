"""Controller for GSF Settings (§9.2).

The feature gate may only open when readiness reports no blocking check (§30.1),
and it must never be opened by install or migrate (§44).
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class GSFSettings(Document):
    def validate(self) -> None:
        if not self.enabled:
            return
        before = self.get_doc_before_save()
        if before and before.enabled:
            return

        from erpnext_ua.group_stock_fifo.services.readiness import readiness

        report = readiness()
        if not report.ready:
            frappe.throw(
                "GSF cannot be enabled while readiness reports blocking checks:<br>"
                + "<br>".join(report.blocking_checks),
                title="GSF_NOT_ENABLED",
            )
