"""Fail-closed Sales Invoice hooks for GSF stock domains."""

from __future__ import annotations

from typing import Any

import frappe

from ..setup.layer_dimension import (
    ALLOCATION_FIELD,
    FULFILLMENT_LOCATION_FIELD,
    FULFILLMENT_SOURCE_FIELD,
    LAYER_FIELD,
    MANAGED_RETURN_FIELD,
    MANAGED_SALE_FIELD,
    RETURN_ORIGIN_LAYER_FIELD,
    SLICE_FIELD,
)
from .layers import gsf_enabled


def validate_managed_sales_invoice(doc: Any, method: str | None = None) -> None:
    del method
    if doc.get(FULFILLMENT_SOURCE_FIELD):
        frappe.throw(
            "This draft is a logical sale request; submit the routed Sales Invoices instead"
        )
    if doc.get(FULFILLMENT_LOCATION_FIELD) and not (
        doc.get(MANAGED_SALE_FIELD) or doc.get(MANAGED_RETURN_FIELD)
    ):
        frappe.throw(
            "Sales at a Global FIFO Physical Location must use Sale Fulfillment"
        )
    if not gsf_enabled():
        return
    roles = _warehouse_roles(doc)
    original_managed = bool(
        doc.is_return
        and doc.return_against
        and frappe.db.get_value("Sales Invoice", doc.return_against, MANAGED_SALE_FIELD)
    )
    if doc.is_return and (original_managed or roles):
        _validate_return(doc, roles, original_managed=original_managed)
        return
    if roles or doc.get(MANAGED_SALE_FIELD):
        _validate_sale(doc, roles)


def before_cancel_managed_sales_invoice(doc: Any, method: str | None = None) -> None:
    del method
    if frappe.flags.in_uninstall:
        return
    if doc.get(MANAGED_SALE_FIELD) or doc.get(MANAGED_RETURN_FIELD):
        frappe.throw(
            "GSF Sales Invoices cannot be cancelled directly; use the controlled return/reversal flow",
            title="MANUAL_REVIEW_REQUIRED",
        )


def _validate_sale(doc: Any, roles: dict[str, str]) -> None:
    if not doc.get(MANAGED_SALE_FIELD):
        frappe.throw("GSF stock must be sold through a managed checkout")
    if doc.is_return or not doc.update_stock:
        frappe.throw("A managed GSF sale must be a stock-updating sale")
    for row in doc.items:
        role = roles.get(row.warehouse)
        if role != "GSF_SALE_STAGE":
            frappe.throw(f"Row {row.idx}: managed GSF sales must consume a Sale Stage")
        _require_sale_trail(row)


def _validate_return(doc: Any, roles: dict[str, str], *, original_managed: bool) -> None:
    if not original_managed or not doc.get(MANAGED_RETURN_FIELD):
        frappe.throw("A return against a GSF sale must use the managed GSF return service")
    original_company = frappe.db.get_value("Sales Invoice", doc.return_against, "company")
    if doc.company != original_company:
        frappe.throw("A GSF return belongs to the company that made the original sale")
    if not doc.update_stock:
        frappe.throw("A managed GSF return must update stock")
    for row in doc.items:
        role = roles.get(row.warehouse)
        if role not in {"GSF_OWN_POOL", "GSF_RETURN_QUARANTINE"}:
            frappe.throw(f"Row {row.idx}: invalid GSF return warehouse")
        if not row.sales_invoice_item or not row.get(RETURN_ORIGIN_LAYER_FIELD):
            frappe.throw(f"Row {row.idx}: GSF return origin is incomplete")
        if not row.get(ALLOCATION_FIELD) or not row.get(SLICE_FIELD):
            frappe.throw(f"Row {row.idx}: GSF allocation trail is incomplete")
        if not row.get(LAYER_FIELD):
            frappe.throw(f"Row {row.idx}: a managed return requires a new GSF layer")


def _require_sale_trail(row: Any) -> None:
    if not row.get(LAYER_FIELD) or not row.get(ALLOCATION_FIELD) or not row.get(SLICE_FIELD):
        frappe.throw(f"Row {row.idx}: managed GSF stock requires Layer, Allocation and Slice")


def _warehouse_roles(doc: Any) -> dict[str, str]:
    warehouses = {row.warehouse for row in doc.items if row.warehouse}
    if not warehouses:
        return {}
    return {
        row.warehouse: row.warehouse_role
        for row in frappe.get_all(
            "GSF Warehouse Binding",
            filters={
                "warehouse": ("in", list(warehouses)),
                "manager_app": "GSF",
                "enabled": 1,
            },
            fields=["warehouse", "warehouse_role"],
        )
    }
