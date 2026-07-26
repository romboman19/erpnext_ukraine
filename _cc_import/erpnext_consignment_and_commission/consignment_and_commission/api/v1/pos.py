"""Versioned authenticated API for persistent split POS checkout."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import frappe
from frappe.utils import getdate

from ...integrations.pos import (
    advance_pos_route,
    compensate_pos_checkout,
    mark_print_job_failed,
    mark_print_job_succeeded,
    prepare_pos_checkout,
    update_pos_payment_state,
)
from ...services.pos_checkout import (
    POSCheckoutError,
    POSCheckoutRequest,
    POSRouteLine,
    POSRouteRequest,
)
from ...services.pos_saga import PaymentTender
from .common import (
    OPERATOR_ROLES,
    assert_permission,
    assert_roles,
    parse_bool,
    parse_decimal,
    parse_json,
)


def _payload(checkout: Any) -> dict[str, Any]:
    routes = frappe.get_all(
        "CC POS Route",
        filters={"checkout": checkout.name},
        fields=[
            "name",
            "group_id",
            "status",
            "fiscal_route",
            "total_amount",
            "sales_invoice",
            "print_job",
            "last_error",
        ],
        order_by="creation asc",
    )
    return {
        "name": checkout.name,
        "status": checkout.status,
        "external_order_doctype": checkout.external_order_doctype,
        "external_order_name": checkout.external_order_name,
        "payment_state": checkout.payment_state,
        "currency": checkout.currency,
        "total_amount": checkout.total_amount,
        "last_error": checkout.last_error,
        "routes": routes,
    }


@frappe.whitelist(methods=["POST"])
def prepare(
    *,
    idempotency_key: str,
    external_order_doctype: str,
    external_order_name: str,
    customer: str,
    posting_date: str,
    currency: str,
    conversion_rate: str | int | float,
    fiscal_checkout: int | bool,
    routes: str | list[dict[str, Any]],
    tenders: str | list[dict[str, Any]],
    lookup_token: str = "",
) -> dict[str, Any]:
    assert_roles(OPERATOR_ROLES)
    assert_permission("Sales Invoice", "create")
    assert_permission("Customer", "read", customer)
    try:
        route_values = parse_json(routes, label="routes")
        tender_values = parse_json(tenders, label="tenders")
        rate = parse_decimal(conversion_rate, label="conversion_rate")
        is_fiscal = parse_bool(fiscal_checkout, label="fiscal_checkout")
    except ValueError as exc:
        raise POSCheckoutError(str(exc)) from exc
    if not isinstance(route_values, list) or not isinstance(tender_values, list):
        raise POSCheckoutError("routes and tenders must be JSON lists")
    parsed_routes = []
    for route_index, route in enumerate(route_values, start=1):
        if not isinstance(route, dict) or not isinstance(route.get("lines"), list):
            raise POSCheckoutError(f"routes[{route_index}] must contain a lines list")
        lines = []
        for line_index, line in enumerate(route["lines"], start=1):
            if not isinstance(line, dict):
                raise POSCheckoutError(
                    f"routes[{route_index}].lines[{line_index}] must be an object"
                )
            try:
                line_rate = parse_decimal(
                    line.get("rate"),
                    label=f"routes[{route_index}].lines[{line_index}].rate",
                )
            except ValueError as exc:
                raise POSCheckoutError(str(exc)) from exc
            lines.append(
                POSRouteLine(
                    allocation=str(line.get("allocation") or ""),
                    rate=line_rate,
                    external_row_id=str(line.get("external_row_id") or ""),
                )
            )
        parsed_routes.append(
            POSRouteRequest(
                group_id=str(route.get("group_id") or ""),
                company=str(route.get("company") or ""),
                location=str(route.get("location") or ""),
                legal_entity_type=str(route.get("legal_entity_type") or ""),
                legal_entity_name=str(route.get("legal_entity_name") or ""),
                fiscal_route=str(route.get("fiscal_route") or ""),
                lines=tuple(lines),
            )
        )
        assert_permission("Company", "read", str(route.get("company") or ""))
        assert_permission("CC Location", "read", str(route.get("location") or ""))
        for line in route["lines"]:
            assert_permission(
                "CC Allocation",
                "read",
                str(line.get("allocation") or ""),
            )
    parsed_tenders = []
    for index, tender in enumerate(tender_values, start=1):
        if not isinstance(tender, dict):
            raise POSCheckoutError(f"tenders[{index}] must be an object")
        try:
            amount = parse_decimal(tender.get("amount"), label=f"tenders[{index}].amount")
        except ValueError as exc:
            raise POSCheckoutError(str(exc)) from exc
        parsed_tenders.append(
            PaymentTender(
                tender_id=str(tender.get("tender_id") or ""),
                mode_of_payment=str(tender.get("mode_of_payment") or ""),
                amount=amount,
            )
        )
    checkout = prepare_pos_checkout(
        POSCheckoutRequest(
            idempotency_key=idempotency_key,
            external_order_doctype=external_order_doctype,
            external_order_name=external_order_name,
            lookup_token=lookup_token,
            customer=customer,
            posting_date=getdate(posting_date),
            currency=currency,
            conversion_rate=Decimal(str(rate)),
            fiscal_checkout=is_fiscal,
            routes=tuple(parsed_routes),
            tenders=tuple(parsed_tenders),
        )
    )
    return _payload(checkout)


@frappe.whitelist(methods=["POST"])
def advance_route(*, route: str) -> dict[str, Any]:
    assert_roles(OPERATOR_ROLES)
    assert_permission("CC POS Route", "read", route)
    assert_permission("Sales Invoice", "create")
    assert_permission("Sales Invoice", "submit")
    document = advance_pos_route(route)
    return _payload(frappe.get_doc("CC POS Checkout", document.checkout))


@frappe.whitelist(methods=["POST"])
def print_succeeded(*, print_job: str, provider_reference: str = "") -> dict[str, Any]:
    assert_roles(OPERATOR_ROLES)
    assert_permission("CC POS Print Job", "read", print_job)
    job = mark_print_job_succeeded(
        print_job,
        provider_reference=provider_reference,
    )
    route = frappe.get_doc("CC POS Route", job.route)
    return _payload(frappe.get_doc("CC POS Checkout", route.checkout))


@frappe.whitelist(methods=["POST"])
def print_failed(*, print_job: str, error: str) -> dict[str, Any]:
    assert_roles(OPERATOR_ROLES)
    assert_permission("CC POS Print Job", "read", print_job)
    job = mark_print_job_failed(print_job, error=error)
    route = frappe.get_doc("CC POS Route", job.route)
    return _payload(frappe.get_doc("CC POS Checkout", route.checkout))


@frappe.whitelist(methods=["POST"])
def set_payment_state(*, checkout: str, state: str) -> dict[str, Any]:
    assert_roles(OPERATOR_ROLES)
    assert_permission("CC POS Checkout", "read", checkout)
    return _payload(update_pos_payment_state(checkout, state))


@frappe.whitelist(methods=["POST"])
def compensate(*, checkout: str, reason: str) -> dict[str, Any]:
    assert_roles(OPERATOR_ROLES)
    assert_permission("CC POS Checkout", "read", checkout)
    assert_permission("Sales Invoice", "cancel")
    return _payload(compensate_pos_checkout(checkout, reason=reason))


@frappe.whitelist(methods=["GET"])
def status(*, checkout: str) -> dict[str, Any]:
    assert_roles(OPERATOR_ROLES)
    assert_permission("CC POS Checkout", "read", checkout)
    return _payload(frappe.get_doc("CC POS Checkout", checkout))
