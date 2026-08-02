"""Channel adapters into the shared sale-fulfillment core."""

from __future__ import annotations

from decimal import Decimal

import frappe
from frappe.utils import get_datetime

from ..setup.layer_dimension import (
    FULFILLMENT_FIELD,
    FULFILLMENT_LOCATION_FIELD,
    FULFILLMENT_SOURCE_FIELD,
)
from .checkout import CheckoutLine, CheckoutRequest, open_checkout, run
from .domain import GSFError
from .serial_identity import single_serial


@frappe.whitelist()
def fulfill_sales_invoice(draft_invoice: str) -> dict:
    """Route one manual draft through the same FIFO used by POS and APIs."""
    source = frappe.get_doc("Sales Invoice", draft_invoice)
    if not frappe.has_permission("Sales Invoice", "write", doc=source):
        frappe.throw("Not permitted to fulfill this Sales Invoice", frappe.PermissionError)
    return _result(fulfill_sales_invoice_document(source))


def fulfill_sales_invoice_document(
    source,
    *,
    sales_channel: str = "MANUAL_INVOICE",
):
    """Internal adapter used by Desk and background ecommerce workers."""
    if source.docstatus != 0:
        frappe.throw("Only a draft Sales Invoice can start sale fulfillment")
    if source.get(FULFILLMENT_SOURCE_FIELD) and source.get(FULFILLMENT_FIELD):
        return frappe.get_doc("GSF Checkout", source.get(FULFILLMENT_FIELD))
    location = source.get(FULFILLMENT_LOCATION_FIELD)
    if not location:
        frappe.throw("Select Global FIFO Physical Location before fulfillment")
    if not source.items:
        frappe.throw("Sales Invoice requires at least one item")

    source_sales_orders = {row.sales_order for row in source.items if row.sales_order}
    external_doctype = "Sales Order" if len(source_sales_orders) == 1 else "Sales Invoice"
    external_name = source_sales_orders.pop() if external_doctype == "Sales Order" else source.name
    checkout = fulfill_lines(
        idempotency_key=f"SALES_INVOICE:{source.name}",
        physical_location=location,
        seller_company=source.company,
        customer=source.customer,
        lines=tuple(_invoice_line(row) for row in source.items),
        sales_channel=sales_channel,
        external_order_doctype=external_doctype,
        external_order_name=external_name,
        currency=source.currency,
        conversion_rate=Decimal(str(source.conversion_rate or 1)),
        posting_datetime=get_datetime(f"{source.posting_date} {source.posting_time or '00:00:00'}"),
    )
    source.set(FULFILLMENT_SOURCE_FIELD, 1)
    source.set(FULFILLMENT_FIELD, checkout.name)
    source.save(ignore_permissions=True)
    return checkout


@frappe.whitelist()
def fulfill_sales_order(sales_order: str) -> dict:
    """Invoice a submitted Sales Order through the same global FIFO routes."""
    source = frappe.get_doc("Sales Order", sales_order)
    if not frappe.has_permission("Sales Order", "write", doc=source):
        frappe.throw("Not permitted to fulfill this Sales Order", frappe.PermissionError)
    if source.docstatus != 1:
        frappe.throw("Submit the Sales Order before Global FIFO fulfillment")
    if source.get(FULFILLMENT_FIELD):
        return _result(frappe.get_doc("GSF Checkout", source.get(FULFILLMENT_FIELD)))
    location = source.get(FULFILLMENT_LOCATION_FIELD)
    if not location:
        frappe.throw("Select Global FIFO Physical Location before fulfillment")
    if not source.items:
        frappe.throw("Sales Order requires at least one item")

    checkout = fulfill_lines(
        idempotency_key=f"SALES_ORDER:{source.name}",
        physical_location=location,
        seller_company=source.company,
        customer=source.customer,
        lines=tuple(_invoice_line(row) for row in source.items),
        sales_channel="SALES_ORDER",
        external_order_doctype="Sales Order",
        external_order_name=source.name,
        currency=source.currency,
        conversion_rate=Decimal(str(source.conversion_rate or 1)),
        posting_datetime=get_datetime(),
    )
    source.db_set(FULFILLMENT_FIELD, checkout.name, update_modified=False)
    return _result(checkout)


def fulfill_lines(
    *,
    idempotency_key: str,
    physical_location: str,
    seller_company: str,
    customer: str,
    lines: tuple[CheckoutLine, ...],
    sales_channel: str,
    external_order_doctype: str | None = None,
    external_order_name: str | None = None,
    currency: str | None = None,
    conversion_rate: Decimal = Decimal("1"),
    posting_datetime=None,
    requires_fiscalization: bool = False,
):
    company_group = frappe.db.get_value(
        "GSF Physical Location",
        {"name": physical_location, "disabled": 0},
        "company_group",
    )
    if not company_group:
        raise GSFError(
            f"Physical Location {physical_location} is not active",
            "LOCATION_NOT_ACTIVE",
        )
    checkout = open_checkout(
        CheckoutRequest(
            idempotency_key=idempotency_key,
            company_group=company_group,
            physical_location=physical_location,
            seller_company=seller_company,
            customer=customer,
            lines=lines,
            external_order_doctype=external_order_doctype,
            external_order_name=external_order_name,
            sales_channel=sales_channel,
            currency=currency,
            conversion_rate=conversion_rate,
            posting_datetime=posting_datetime,
            requires_fiscalization=requires_fiscalization,
        )
    )
    checkout = run(checkout.name)
    if not checkout.sales_invoices:
        raise GSFError(
            f"Sale fulfillment {checkout.name} stopped at {checkout.status}",
            checkout.failure_code or "MANUAL_REVIEW_REQUIRED",
        )
    return checkout


def _invoice_line(row) -> CheckoutLine:
    qty = Decimal(str(row.qty))
    visible_rate = Decimal(str(row.price_list_rate or row.rate or 0))
    discount = qty * visible_rate * Decimal(str(row.discount_percentage or 0)) / Decimal("100")
    return CheckoutLine(
        item_code=row.item_code,
        qty=qty,
        rate=visible_rate,
        external_row_id=row.get("so_detail") or row.name,
        uom=row.uom,
        barcode=row.get("barcode"),
        serial_no=single_serial(row.get("serial_no")),
        batch_no=row.get("batch_no"),
        discount_amount=discount,
    )


def _result(checkout) -> dict:
    import json

    return {
        "status": checkout.status,
        "fulfillment": checkout.name,
        "sales_invoices": json.loads(checkout.sales_invoices or "[]"),
        "routes": json.loads(checkout.route_manifest or "[]"),
    }
