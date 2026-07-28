"""Controller for GSF Staging Lane (§9.8, ADR-006)."""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from erpnext_ua.group_stock_fifo.services.domain import LANE_AVAILABLE, LANE_DISABLED


class GSFStagingLane(Document):
    def validate(self) -> None:
        if not self.enabled:
            self.status = LANE_DISABLED
        elif self.status == LANE_DISABLED:
            self.status = LANE_AVAILABLE
        if self.status != "LOCKED":
            self.current_checkout = None
            self.lock_token = None
        binding = frappe.db.get_value(
            "GSF Warehouse Binding", {"warehouse": self.warehouse, "enabled": 1},
            ["manager_app", "warehouse_role"], as_dict=True,
        )
        if binding and (binding.manager_app != "GSF" or binding.warehouse_role != "GSF_SALE_STAGE"):
            frappe.throw(
                f"Warehouse {self.warehouse} is bound as {binding.manager_app}/"
                f"{binding.warehouse_role} and cannot be a staging lane",
                title="WAREHOUSE_DOMAIN_CONFLICT",
            )
