"""Post every legal route selected by one channel-neutral fulfillment."""

from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal
from typing import Any

import frappe
from frappe.utils import get_datetime

from erpnext_ua.consignment_and_commission.integrations.sales_invoice import (
    create_sales_invoice_from_allocations,
)
from erpnext_ua.consignment_and_commission.services.sale import (
    ManagedSaleLine,
    ManagedSaleRequest,
)

from ..setup.layer_dimension import FULFILLMENT_ROUTE_FIELD
from .fulfillment_domain import ProviderAllocationRef, effective_rate
from .fulfillment_payments import split_pos_payments
from .fulfillment_pos_routes import active_route_shift, cash_desk_for_route
from .fulfillment_reservation import checkout_refs
from .sale import SaleLine, sell
from .staging import release_lane
from .stock_domain_runtime import GSF_PROVIDER_ID


def post_fulfillment(checkout: Any) -> list[Any]:
    refs = checkout_refs(checkout)
    if not refs:
        raise ValueError(f"Fulfillment {checkout.name} has no provider allocations")
    grouped = _group_refs(refs)
    payment_plan = _payment_plan(checkout, grouped)
    invoices: list[Any] = []

    for route_id, route_refs in grouped.items():
        provider_id = route_refs[0].route.provider_id
        invoice_values = _invoice_values(checkout, route_refs[0].route, payment_plan)
        if provider_id == GSF_PROVIDER_ID:
            invoices.append(_post_gsf(checkout, route_refs, invoice_values))
        else:
            invoices.append(_post_external(checkout, route_id, route_refs, invoice_values))

    if not any(ref.route.provider_id == GSF_PROVIDER_ID for ref in refs):
        release_lane(checkout.staging_lane, checkout=checkout.name)
    return invoices


def _group_refs(
    refs: list[ProviderAllocationRef],
) -> OrderedDict[str, list[ProviderAllocationRef]]:
    grouped: OrderedDict[str, list[ProviderAllocationRef]] = OrderedDict()
    for ref in refs:
        grouped.setdefault(ref.route.stable_id, []).append(ref)
    return grouped


def _route_total(refs: list[ProviderAllocationRef]) -> Decimal:
    return sum(
        (ref.qty * ref.rate - ref.discount_amount for ref in refs),
        Decimal("0"),
    )


def _payment_plan(
    checkout: Any,
    grouped: OrderedDict[str, list[ProviderAllocationRef]],
) -> dict[str, list[dict]]:
    if checkout.external_order_doctype != "POS Order" or not checkout.external_order_name:
        return {}
    order = frappe.get_doc("POS Order", checkout.external_order_name)
    from erpnext_ua.ua_gift_certificates.services.fulfillment import (
        sale_route_payment_components,
    )

    return split_pos_payments(
        order,
        OrderedDict((route_id, _route_total(refs)) for route_id, refs in grouped.items()),
        fixed_route_payments=sale_route_payment_components(order, checkout),
    )


def _invoice_values(
    checkout: Any,
    route: Any,
    payment_plan: dict[str, list[dict]],
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "gsf_checkout": checkout.name,
        "ua_sale_fulfillment": checkout.name,
        FULFILLMENT_ROUTE_FIELD: route.stable_id,
        "remarks": f"Global sale fulfillment {checkout.name}; route {route.stable_id}",
    }
    if route.legal_entity_type == "FOP Profile":
        values["ua_fop_profile"] = route.legal_entity_name
    if checkout.external_order_doctype != "POS Order" or not checkout.external_order_name:
        return values
    order = frappe.get_doc("POS Order", checkout.external_order_name)
    default_desk = frappe.get_doc("POS Cash Desk", order.cash_desk)
    cash_desk = cash_desk_for_route(route, default_desk)
    values.update(
        {
            "is_pos": 1,
            "ua_pos_order": order.name,
            "ua_pos_desk": cash_desk,
            "ua_pos_shift": active_route_shift(cash_desk),
            "payments": payment_plan.get(route.stable_id, []),
            "change_amount": 0,
        }
    )
    return values


def _post_gsf(
    checkout: Any,
    refs: list[ProviderAllocationRef],
    invoice_values: dict[str, Any],
) -> Any:
    return sell(
        [
            SaleLine(
                allocation=ref.allocation_name,
                rate=ref.rate,
                discount_amount=ref.discount_amount,
            )
            for ref in refs
        ],
        customer=checkout.customer,
        checkout=checkout.name,
        invoice_values=invoice_values,
    )


def _post_external(
    checkout: Any,
    route_id: str,
    refs: list[ProviderAllocationRef],
    invoice_values: dict[str, Any],
) -> Any:
    route = refs[0].route
    link_sales_order = _can_link_sales_order(checkout, route.seller_company)
    request = ManagedSaleRequest(
        idempotency_key=f"{checkout.idempotency_key}:sale:{route_id}",
        customer=checkout.customer,
        posting_date=(str(get_datetime(checkout.posting_datetime).date()) if checkout.posting_datetime else None),
        currency=checkout.currency or None,
        conversion_rate=(Decimal(str(checkout.conversion_rate)) if checkout.currency else None),
        lines=tuple(
            ManagedSaleLine(
                ref.allocation_name,
                effective_rate(
                    qty=ref.qty,
                    rate=ref.rate,
                    discount_amount=ref.discount_amount,
                ),
            )
            for ref in refs
        ),
    )
    invoice = create_sales_invoice_from_allocations(
        request,
        invoice_values=invoice_values,
        allow_transaction_writes=True,
    )
    for ref in refs:
        for row in invoice.items:
            if row.get("cc_allocation") == ref.allocation_name:
                row.set("ua_pos_order_item", ref.external_row_id)
                if link_sales_order:
                    row.sales_order = checkout.external_order_name
                    row.so_detail = ref.external_row_id
    if invoice.company != route.seller_company:
        raise ValueError(
            f"Route {route_id} expected {route.seller_company}, got invoice {invoice.company}"
        )
    if invoice.docstatus == 0:
        invoice.flags.ignore_permissions = True
        invoice.submit()
    return invoice


def _can_link_sales_order(checkout: Any, invoice_company: str) -> bool:
    if checkout.external_order_doctype != "Sales Order" or not checkout.external_order_name:
        return False
    source_company = frappe.db.get_value("Sales Order", checkout.external_order_name, "company")
    return source_company == invoice_company
