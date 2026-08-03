"""Controller for GSF Warehouse Binding (§8).

This registry is the only authority on which stock domain owns a warehouse
(ADR-001), so its validation is the enforcement point for §4.1 and §8.2.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from erpnext_ua.group_stock_fifo.services.domain import (
    BindingRequest,
    GSFError,
    WarehouseFacts,
    validate_binding,
)


class GSFWarehouseBinding(Document):
    def validate(self) -> None:
        warehouse = frappe.db.get_value(
            "Warehouse", self.warehouse, ["company", "is_group", "disabled"], as_dict=True
        )
        if not warehouse:
            frappe.throw(f"Warehouse {self.warehouse} not found", title="WAREHOUSE_BINDING_MISSING")

        previous = self.get_doc_before_save()
        request = BindingRequest(
            warehouse=WarehouseFacts(
                name=self.warehouse,
                company=warehouse.company,
                is_group=bool(warehouse.is_group),
                disabled=bool(warehouse.disabled),
            ),
            company=self.company,
            warehouse_role=self.warehouse_role,
            manager_app=self.manager_app,
            existing_binding_app=self._foreign_domain(),
            has_stock_movements=bool(self.first_stock_posting),
            previous_role=previous.warehouse_role if previous else None,
        )
        try:
            validate_binding(request)
        except GSFError as error:
            frappe.throw(str(error), title=error.code)

    def _foreign_domain(self) -> str | None:
        """The domain that already owns this warehouse, if it is not this row.

        `autoname` is `field:warehouse`, so a new row for an already-bound
        warehouse arrives carrying the *existing* row's name. Excluding by name
        unconditionally would therefore hide exactly the conflict we are looking
        for, and the insert would fail later on the unique index with a generic
        platform error instead of a §33 code.
        """
        filters = {"warehouse": self.warehouse, "enabled": 1}
        if not self.is_new():
            filters["name"] = ("!=", self.name)
        other = frappe.db.get_value("GSF Warehouse Binding", filters, "manager_app")
        if other:
            return other
        if self.manager_app == "GSF" and _is_cc_warehouse(self.warehouse):
            return "CC"
        return None


def _is_cc_warehouse(warehouse: str) -> bool:
    """§8.3: a CC Location warehouse is never available to GSF."""
    if not frappe.db.exists("DocType", "CC Location"):
        return False
    return bool(
        frappe.db.sql(
            """
            select 1 from `tabCC Location`
            where own_warehouse = %(w)s or commission_warehouse = %(w)s
               or consignment_warehouse = %(w)s
            limit 1
            """,
            {"w": warehouse},
        )
    )
