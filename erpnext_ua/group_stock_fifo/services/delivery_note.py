"""Fail closed when a standard Delivery Note would bypass allocation lineage."""

from __future__ import annotations

from typing import Any


def validate_managed_warehouses(doc: Any, method: str | None = None) -> None:
    del method
    import frappe

    warehouses = {row.warehouse for row in doc.items if row.warehouse}
    if not warehouses:
        return
    gsf = set(
        frappe.get_all(
            "GSF Warehouse Binding",
            filters={
                "warehouse": ("in", list(warehouses)),
                "manager_app": "GSF",
                "enabled": 1,
            },
            pluck="warehouse",
        )
    )
    cc = set()
    for location in frappe.get_all(
        "CC Location",
        filters={"disabled": 0},
        fields=["own_warehouse", "commission_warehouse", "consignment_warehouse"],
    ):
        cc.update(
            warehouse
            for warehouse in (
                location.own_warehouse,
                location.commission_warehouse,
                location.consignment_warehouse,
            )
            if warehouse in warehouses
        )
    managed = sorted(gsf | cc)
    if managed:
        frappe.throw(
            "Delivery Note cannot issue Global FIFO/commission stock directly. "
            "Use Global FIFO fulfillment from Sales Order or Sales Invoice so the exact "
            f"Company, cost layer and return lineage are preserved: {', '.join(managed)}"
        )
