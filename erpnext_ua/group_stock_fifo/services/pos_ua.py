"""Adapter between the POS-UA customer saga and GSF stock services (ADR-015)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import frappe

from ..setup.layer_dimension import ALLOCATION_FIELD, MANAGED_SALE_FIELD
from .domain import OWN_POOL_ROLE, GSFError
from .layers import gsf_enabled
from .pos_return_domain import ReturnLine, consume_return_rows
from .returns import accept_return, returned_qty
from .serial_identity import single_serial


@dataclass(frozen=True, slots=True)
class POSScope:
    company_group: str
    physical_location: str
    company: str


def scope_for_desk(desk: Any) -> POSScope | None:
    """Return the GSF scope only for an explicitly bound POS warehouse."""
    if not gsf_enabled():
        return None
    binding = frappe.db.get_value(
        "GSF Warehouse Binding",
        {
            "warehouse": desk.warehouse,
            "manager_app": "GSF",
            "warehouse_role": OWN_POOL_ROLE,
            "enabled": 1,
        },
        ["company_group", "physical_location", "company"],
        as_dict=True,
    )
    if not binding:
        return None
    if binding.company != desk.company:
        raise GSFError(
            f"POS desk {desk.name} belongs to {desk.company}, but {desk.warehouse} belongs to {binding.company}",
            "WAREHOUSE_DOMAIN_CONFLICT",
        )
    can_sell = frappe.db.exists(
        "GSF Location Company Binding",
        {
            "company_group": binding.company_group,
            "physical_location": binding.physical_location,
            "company": binding.company,
            "enabled": 1,
            "can_sell": 1,
        },
    )
    if not can_sell:
        raise GSFError(f"{binding.company} cannot sell in this GSF location", "COMPANY_NOT_GROUP_MEMBER")
    return POSScope(binding.company_group, binding.physical_location, binding.company)


def post_sale(order: Any, desk: Any) -> Any:
    """Run a paid POS sale through the persistent global-FIFO checkout."""
    from .checkout import CheckoutLine, CheckoutRequest, open_checkout, run

    scope = scope_for_desk(desk)
    if not scope:
        raise GSFError(f"POS desk {desk.name} is not GSF-bound", "WAREHOUSE_BINDING_MISSING")
    _validate_stock_uoms(order)
    checkout = open_checkout(
        CheckoutRequest(
            idempotency_key=f"UA_POS:{order.name}",
            company_group=scope.company_group,
            physical_location=scope.physical_location,
            seller_company=scope.company,
            customer=order.customer,
            external_order_doctype="POS Order",
            external_order_name=order.name,
            requires_fiscalization=order.fiscal_mode == "Fiscal",
            lines=tuple(
                CheckoutLine(
                    item_code=row.item_code,
                    qty=Decimal(str(row.qty)),
                    rate=Decimal(str(row.rate)),
                    external_row_id=row.name,
                    uom=row.uom,
                    barcode=row.barcode,
                    serial_no=single_serial(row.serial_no),
                    batch_no=row.batch_no,
                    discount_amount=Decimal(str(row.discount_amount or 0)),
                )
                for row in order.items
            ),
        )
    )
    checkout = run(checkout.name)
    if not checkout.sales_invoice:
        raise GSFError(
            f"GSF checkout {checkout.name} stopped at {checkout.status} without a Sales Invoice",
            checkout.failure_code or "MANUAL_REVIEW_REQUIRED",
        )
    order.gsf_checkout = checkout.name
    return frappe.get_doc("Sales Invoice", checkout.sales_invoice)


def post_return(order: Any, desk: Any) -> Any:
    """Return exact technical rows from the receipt's original GSF sale."""
    original_order = frappe.get_doc("POS Order", order.return_against)
    scope = scope_for_desk(desk)
    if not scope:
        raise GSFError(f"Return desk {desk.name} is not GSF-bound", "WAREHOUSE_BINDING_MISSING")
    if not original_order.sales_invoice:
        raise GSFError("The original POS receipt has no Sales Invoice", "MANUAL_REVIEW_REQUIRED")
    original = frappe.get_doc("Sales Invoice", original_order.sales_invoice)
    if not original.get(MANAGED_SALE_FIELD):
        raise GSFError(f"{original.name} is not a GSF sale", "MANUAL_REVIEW_REQUIRED")
    if original.company != desk.company:
        raise GSFError(
            f"Return desk company {desk.company} differs from seller {original.company}",
            "WAREHOUSE_DOMAIN_CONFLICT",
        )
    lines = _plan_return_lines(original_order, original, order)
    return accept_return(
        sales_invoice=original.name,
        lines=lines,
        invoice_values=return_invoice_values(order),
    )


def sale_invoice_values(pos_order: str) -> dict[str, Any]:
    return _invoice_values(frappe.get_doc("POS Order", pos_order), is_return=False)


def return_invoice_values(order: Any) -> dict[str, Any]:
    return _invoice_values(order, is_return=True)


def _invoice_values(order: Any, *, is_return: bool) -> dict[str, Any]:
    from erpnext_ua.ua_gift_certificates.adapters.accounting import invoice_payments

    payments = invoice_payments(order, is_return=is_return)
    return {
        "is_pos": 1,
        "ua_pos_order": order.name,
        "ua_pos_desk": order.cash_desk,
        "ua_pos_shift": order.operational_shift,
        "payments": payments,
        "change_amount": order.change_amount,
    }


def is_gsf_return(order: Any) -> bool:
    if order.order_type != "Return" or not order.return_against:
        return False
    invoice = frappe.db.get_value("POS Order", order.return_against, "sales_invoice")
    return bool(invoice and frappe.db.get_value("Sales Invoice", invoice, MANAGED_SALE_FIELD))


def record_fiscal_result(order: Any, *, state: str, receipt: str | None = None) -> None:
    if not order.gsf_checkout:
        return
    from .checkout import record_fiscal_result as record_checkout_fiscal_result

    record_checkout_fiscal_result(order.gsf_checkout, fiscal_state=state, prro_receipt=receipt)


def _plan_return_lines(original_order: Any, original: Any, return_order: Any) -> list[ReturnLine]:
    invoice_rows = _invoice_rows_by_pos_item(original)
    all_rows = {row.name for rows in invoice_rows.values() for row in rows}
    prior = returned_qty(original.name, all_rows)
    return consume_return_rows(invoice_rows, return_order.items, prior)


def _validate_stock_uoms(order: Any) -> None:
    """Validate stock UOM and the tracked identity before reserving anything."""
    item_codes = {row.item_code for row in order.items}
    items = {
        row.name: row
        for row in frappe.get_all(
            "Item",
            filters={"name": ("in", list(item_codes))},
            fields=["name", "stock_uom", "has_serial_no", "has_batch_no"],
        )
    }
    seen_serials: set[str] = set()
    for row in order.items:
        item = items.get(row.item_code)
        if not item:
            raise GSFError(f"Unknown item {row.item_code}", "MANUAL_REVIEW_REQUIRED")
        if row.uom != item.stock_uom:
            raise GSFError(
                f"POS row {row.name} uses {row.uom}; GSF requires stock UOM {item.stock_uom}",
                "MANUAL_REVIEW_REQUIRED",
            )
        serial_no = single_serial(row.serial_no)
        if item.has_serial_no:
            if not serial_no:
                raise GSFError(
                    f"POS row {row.name} requires a scanned Serial No",
                    "SERIAL_AMBIGUOUS",
                )
            if Decimal(str(row.qty)) != Decimal("1"):
                raise GSFError(
                    f"POS row {row.name} must contain one unit for Serial No {serial_no}",
                    "SERIAL_AMBIGUOUS",
                )
            if serial_no in seen_serials:
                raise GSFError(
                    f"Serial No {serial_no} appears more than once in the basket",
                    "SERIAL_AMBIGUOUS",
                )
            seen_serials.add(serial_no)
        elif serial_no:
            raise GSFError(
                f"POS row {row.name} supplies Serial No {serial_no} for an untracked item",
                "SERIAL_AMBIGUOUS",
            )
        if not item.has_batch_no and row.batch_no:
            raise GSFError(
                f"POS row {row.name} supplies Batch {row.batch_no} for a non-batch item",
                "BATCH_MISMATCH",
            )


def _invoice_rows_by_pos_item(original: Any) -> dict[str, list[Any]]:
    allocation_names = {row.get(ALLOCATION_FIELD) for row in original.items if row.get(ALLOCATION_FIELD)}
    if not allocation_names:
        raise GSFError(f"GSF sale {original.name} has no allocations", "MANUAL_REVIEW_REQUIRED")
    external_rows = {
        row.name: row.external_row_id
        for row in frappe.get_all(
            "GSF Allocation",
            filters={"name": ("in", list(allocation_names))},
            fields=["name", "external_row_id"],
        )
    }
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in sorted(original.items, key=lambda item: item.idx):
        external_row = external_rows.get(row.get(ALLOCATION_FIELD))
        if not external_row:
            raise GSFError(f"Invoice row {row.name} has no POS row identity", "MANUAL_REVIEW_REQUIRED")
        grouped[external_row].append(row)
    return grouped
