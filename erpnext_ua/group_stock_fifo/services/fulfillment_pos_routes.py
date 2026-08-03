"""Resolve the POS-UA desk and management shift for each legal sale route."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import frappe

from .domain import GSFError
from .fulfillment_domain import FulfillmentRouteKey


@dataclass(frozen=True, slots=True)
class PostedPOSRoute:
    invoice: Any
    route: FulfillmentRouteKey
    cash_desk: str
    operational_shift: str


def cash_desk_for_route(route: FulfillmentRouteKey, default_desk: Any) -> str:
    if route.provider_id == "GSF":
        if default_desk.company != route.seller_company:
            raise GSFError(
                f"GSF route seller {route.seller_company} differs from POS desk "
                f"Company {default_desk.company}",
                "WAREHOUSE_DOMAIN_CONFLICT",
            )
        return default_desk.name
    if route.provider_id == "CC":
        cash_desk = frappe.db.get_value("CC Location", route.provider_location, "pos_cash_desk")
        if not cash_desk:
            raise GSFError(
                f"CC Location {route.provider_location} requires a POS Cash Desk for POS-UA sales",
                "WAREHOUSE_BINDING_MISSING",
            )
        state = frappe.db.get_value(
            "POS Cash Desk",
            cash_desk,
            ["company", "status"],
            as_dict=True,
        )
        if not state or state.status != "Active" or state.company != route.seller_company:
            raise GSFError(
                f"POS Cash Desk {cash_desk} is not active for {route.seller_company}",
                "WAREHOUSE_DOMAIN_CONFLICT",
            )
        return cash_desk
    raise GSFError(
        f"Stock provider {route.provider_id} has no POS-UA route adapter",
        "MIXED_STOCK_ROUTE_REQUIRED",
    )


def active_route_shift(cash_desk: str) -> str:
    from erpnext_ua.ua_pos.services.common import active_shift

    shift = active_shift(cash_desk)
    if not shift:
        raise GSFError(
            f"POS Cash Desk {cash_desk} has no open operational shift",
            "MANUAL_REVIEW_REQUIRED",
        )
    return shift


def posted_pos_routes(order: Any, default_desk: Any) -> list[PostedPOSRoute]:
    names = json.loads(order.sales_invoices_json or "[]")
    if not names and order.sales_invoice:
        names = [order.sales_invoice]
    if not names:
        raise GSFError(f"POS Order {order.name} has no Sales Invoices", "MANUAL_REVIEW_REQUIRED")
    checkout_name = order.gsf_checkout
    if not checkout_name and order.order_type == "Return" and order.return_against:
        checkout_name = frappe.db.get_value("POS Order", order.return_against, "gsf_checkout")
    if not checkout_name:
        route = FulfillmentRouteKey(
            provider_id="LEGACY",
            seller_company=default_desk.company,
            provider_location=default_desk.name,
            legal_entity_type="Company",
            legal_entity_name=default_desk.company,
            fiscal_route="FISCAL" if order.fiscal_mode == "Fiscal" else "NON_FISCAL",
        )
        return [
            PostedPOSRoute(
                invoice=frappe.get_doc("Sales Invoice", names[0]),
                route=route,
                cash_desk=default_desk.name,
                operational_shift=order.operational_shift,
            )
        ]

    checkout = frappe.get_doc("GSF Checkout", checkout_name)
    manifest = {
        row["route_id"]: FulfillmentRouteKey(
            provider_id=row["provider_id"],
            seller_company=row["seller_company"],
            provider_location=row["provider_location"],
            legal_entity_type=row["legal_entity_type"],
            legal_entity_name=row["legal_entity_name"],
            fiscal_route=row["fiscal_route"],
        )
        for row in json.loads(checkout.route_manifest or "[]")
    }
    result: list[PostedPOSRoute] = []
    for name in names:
        invoice = frappe.get_doc("Sales Invoice", name)
        route_id = invoice.get("ua_fulfillment_route")
        route = manifest.get(route_id)
        if not route:
            raise GSFError(
                f"Sales Invoice {name} has no immutable fulfillment route",
                "MANUAL_REVIEW_REQUIRED",
            )
        if invoice.company != route.seller_company:
            raise GSFError(
                f"Sales Invoice {name} Company differs from route {route_id}",
                "WAREHOUSE_DOMAIN_CONFLICT",
            )
        cash_desk = cash_desk_for_route(route, default_desk)
        result.append(
            PostedPOSRoute(
                invoice=invoice,
                route=route,
                cash_desk=cash_desk,
                operational_shift=active_route_shift(cash_desk),
            )
        )
    return result
