"""Controller for GSF Layer Balance (§9.10).

One row per layer/company/warehouse position. The row is a *cache* over the
Stock Ledger Entry aggregate keyed on `gsf_stock_layer`; §9.10 is explicit that
it may lag but may never hide a divergence, so the controller keeps the derived
fields consistent and leaves detection of drift to the integrity check.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from erpnext_ua.group_stock_fifo.services.domain import balance_identity


class GSFLayerBalance(Document):
    def autoname(self) -> None:
        """§9.10 key. Making it the name is what makes the position unique."""
        self.name = balance_identity(
            stock_layer=self.stock_layer, company=self.company, warehouse=self.warehouse
        )

    def validate(self) -> None:
        self.available_qty_cache = (self.actual_qty_cache or 0) - (self.reserved_qty_cache or 0)
        if self.available_qty_cache < 0:
            frappe.throw(
                f"Layer {self.stock_layer} has {self.reserved_qty_cache} reserved against "
                f"{self.actual_qty_cache} on hand in {self.warehouse}",
                title="NEGATIVE_STOCK_RISK",
            )
