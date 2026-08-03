"""Gift-certificate allocation across final multi-Company fulfillment routes."""

from __future__ import annotations

import json
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from decimal import Decimal

import frappe

from erpnext_ua.group_stock_fifo.services.fulfillment_benefits import (
    route_quantities,
    split_route_amount,
)
from erpnext_ua.group_stock_fifo.services.fulfillment_reservation import checkout_refs
from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError
from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money


@dataclass(frozen=True, slots=True)
class GiftRouteSlice:
    route_id: str
    pos_order_item: str
    qty: Decimal
    amount: Decimal
    paid: Decimal
    promotional: Decimal
    sequence: int


def reservation_plan(checkout, reservation) -> list[GiftRouteSlice]:
    payload = json.loads(reservation.policy_snapshot_json)
    raw: list[tuple[str, str, Decimal, Decimal]] = []
    for allocation in payload.get("allocations") or []:
        pos_row = allocation["row"]
        quantities = route_quantities(checkout, pos_row)
        if not quantities:
            raise GiftCertificateError(
                f"Gift Certificate allocation row {pos_row} has no final fulfillment route",
                "CERT_POSTING_INCOMPLETE",
            )
        route_amounts = split_route_amount(money(allocation["amount"]), quantities)
        raw.extend(
            (route_id, pos_row, quantities[route_id], amount)
            for route_id, amount in route_amounts.items()
            if amount > ZERO
        )
    requested = money(reservation.requested_amount)
    if money(sum((row[3] for row in raw), ZERO)) != requested:
        raise GiftCertificateError(
            "Gift Certificate fulfillment slices do not match the reservation",
            "CERT_POSTING_INCOMPLETE",
        )
    paid_parts = _split_weighted(money(reservation.paid_component_reserved), [row[3] for row in raw])
    return [
        GiftRouteSlice(
            route_id=route_id,
            pos_order_item=pos_row,
            qty=qty,
            amount=amount,
            paid=paid,
            promotional=money(amount - paid),
            sequence=index,
        )
        for index, ((route_id, pos_row, qty, amount), paid) in enumerate(
            zip(raw, paid_parts, strict=True),
            1,
        )
    ]


def invoice_plan(order, invoice) -> list[tuple[object, GiftRouteSlice]]:
    fulfillment = invoice.get("ua_sale_fulfillment")
    route_id = invoice.get("ua_fulfillment_route")
    if not fulfillment or not route_id:
        return []
    checkout = frappe.get_doc("GSF Checkout", fulfillment)
    result = []
    for reservation in _reservations(order.name):
        result.extend(
            (reservation, row)
            for row in reservation_plan(checkout, reservation)
            if row.route_id == route_id
        )
    return result


def invoice_summary(order, invoice) -> dict:
    rows: dict[str, dict[str, Decimal | int]] = defaultdict(
        lambda: {"amount": ZERO, "paid": ZERO, "promotional": ZERO, "count": 0}
    )
    total = paid = promotional = ZERO
    for _reservation, part in invoice_plan(order, invoice):
        summary = rows[part.pos_order_item]
        summary["amount"] = money(summary["amount"] + part.amount)
        summary["paid"] = money(summary["paid"] + part.paid)
        summary["promotional"] = money(summary["promotional"] + part.promotional)
        summary["count"] = int(summary["count"]) + 1
        total = money(total + part.amount)
        paid = money(paid + part.paid)
        promotional = money(promotional + part.promotional)
    return {"total": total, "paid": paid, "promotional": promotional, "rows": dict(rows)}


def return_invoice_summary(order, invoice) -> dict:
    snapshot = json.loads(order.gift_certificate_snapshot_json or "{}")
    invoice_rows = {row.get("ua_pos_order_item") for row in invoice.items}
    rows: dict[str, dict[str, Decimal | int]] = defaultdict(
        lambda: {"amount": ZERO, "paid": ZERO, "promotional": ZERO, "count": 0}
    )
    total = paid = promotional = ZERO
    for component in snapshot.get("components") or []:
        original_invoice = component.get("sales_invoice") or frappe.db.get_value(
            "UA Gift Certificate Redemption Allocation",
            component["allocation"],
            "sales_invoice",
        )
        return_row = component.get("return_row")
        if original_invoice != invoice.return_against or return_row not in invoice_rows:
            continue
        summary = rows[return_row]
        amount = money(component["amount"])
        paid_part = money(component["paid"])
        promotional_part = money(component["promotional"])
        summary["amount"] = money(summary["amount"] + amount)
        summary["paid"] = money(summary["paid"] + paid_part)
        summary["promotional"] = money(summary["promotional"] + promotional_part)
        summary["count"] = int(summary["count"]) + 1
        total = money(total + amount)
        paid = money(paid + paid_part)
        promotional = money(promotional + promotional_part)
    return {"total": total, "paid": paid, "promotional": promotional, "rows": dict(rows)}


def sale_route_payment_components(order, checkout) -> dict[str, list[dict]]:
    """Return accounting payment modes whose totals exactly match each legal route."""
    from erpnext_ua.ua_gift_certificates.adapters.accounting import (
        _mode,
        _profile,
        _redeemer_profile,
    )

    routes = {ref.route.stable_id: ref.route for ref in checkout_refs(checkout)}
    totals: dict[str, OrderedDict[str, Decimal]] = defaultdict(OrderedDict)
    for payment in order.payments_plan:
        if payment.status != "Confirmed" or payment.kind != "Gift Certificate":
            continue
        reservation = frappe.get_doc("UA Gift Certificate Reservation", payment.gift_certificate_reservation)
        certificate = frappe.get_doc("UA Gift Certificate", reservation.certificate)
        for part in reservation_plan(checkout, reservation):
            route = routes[part.route_id]
            fop_profile = (
                route.legal_entity_name if route.legal_entity_type == "FOP Profile" else None
            )
            same_entity = certificate.issuer_company == route.seller_company and (
                (certificate.issuer_fop_profile or None) == (fop_profile or None)
            )
            components = []
            if same_entity:
                profile_name = frappe.db.get_value(
                    "UA Gift Certificate Program",
                    certificate.program,
                    "accounting_profile",
                )
                profile = _profile(profile_name)
                if part.paid > ZERO:
                    components.append((_mode(profile, "Paid Liability", profile.paid_liability_account), part.paid))
                if part.promotional > ZERO:
                    components.append(
                        (_mode(profile, "Promotional", profile.promotional_expense_account), part.promotional)
                    )
            else:
                profile = _redeemer_profile(certificate, route.seller_company, fop_profile)
                components.append(
                    (
                        _mode(
                            profile,
                            "Settlement Receivable",
                            profile.settlement_receivable_account,
                        ),
                        part.amount,
                    )
                )
            for mode, amount in components:
                current = totals[part.route_id].get(mode, ZERO)
                totals[part.route_id][mode] = money(current + amount)
    return {
        route_id: [
            {"mode_of_payment": mode, "amount": amount}
            for mode, amount in components.items()
            if amount > ZERO
        ]
        for route_id, components in totals.items()
    }


def return_route_payment_components(
    order,
    invoice_routes: dict[str, str],
) -> dict[str, list[dict]]:
    from erpnext_ua.ua_gift_certificates.adapters.accounting import (
        _mode,
        _profile,
        _redeemer_profile,
    )

    totals: dict[str, OrderedDict[str, Decimal]] = defaultdict(OrderedDict)
    for payment in order.payments_plan:
        if payment.status != "Confirmed" or payment.kind != "Gift Certificate":
            continue
        allocation = frappe.db.get_value(
            "UA Gift Certificate Redemption Allocation",
            payment.gift_certificate_redemption_allocation,
            ["certificate", "sales_invoice", "redeemer_company", "redeemer_fop_profile"],
            as_dict=True,
        )
        if not allocation or allocation.sales_invoice not in invoice_routes:
            raise GiftCertificateError(
                "Gift Certificate return payment has no original legal route",
                "CERT_POSTING_INCOMPLETE",
            )
        route_id = invoice_routes[allocation.sales_invoice]
        certificate = frappe.get_doc("UA Gift Certificate", allocation.certificate)
        amount = money(payment.amount)
        paid = money(payment.gift_certificate_paid_amount)
        promotional = money(payment.gift_certificate_promotional_amount)
        same_entity = certificate.issuer_company == allocation.redeemer_company and (
            (certificate.issuer_fop_profile or None) == (allocation.redeemer_fop_profile or None)
        )
        components = []
        if same_entity:
            profile_name = frappe.db.get_value(
                "UA Gift Certificate Program",
                certificate.program,
                "accounting_profile",
            )
            profile = _profile(profile_name)
            if paid > ZERO:
                components.append((_mode(profile, "Paid Liability", profile.paid_liability_account), paid))
            if promotional > ZERO:
                components.append(
                    (_mode(profile, "Promotional", profile.promotional_expense_account), promotional)
                )
        else:
            profile = _redeemer_profile(
                certificate,
                allocation.redeemer_company,
                allocation.redeemer_fop_profile,
            )
            components.append(
                (
                    _mode(
                        profile,
                        "Settlement Receivable",
                        profile.settlement_receivable_account,
                    ),
                    amount,
                )
            )
        for mode, component_amount in components:
            current = totals[route_id].get(mode, ZERO)
            totals[route_id][mode] = money(current + component_amount)
    return {
        route_id: [
            {"mode_of_payment": mode, "amount": amount}
            for mode, amount in components.items()
            if amount > ZERO
        ]
        for route_id, components in totals.items()
    }


def _reservations(order_name: str) -> list:
    names = frappe.get_all(
        "UA Gift Certificate Reservation",
        filters={"pos_order": order_name, "status": ("in", ["Active", "Consuming", "Consumed"])},
        pluck="name",
        order_by="certificate, creation",
    )
    return [frappe.get_doc("UA Gift Certificate Reservation", name) for name in names]


def _split_weighted(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    target = money(total)
    weight_total = money(sum(weights, ZERO))
    if target < ZERO or target > weight_total or not weights:
        raise GiftCertificateError(
            "Gift Certificate funding components are invalid",
            "CERT_POSTING_INCOMPLETE",
        )
    result = [min(money(target * money(weight) / weight_total), money(weight)) for weight in weights]
    residual = money(target - sum(result, ZERO))
    for index in range(len(result) - 1, -1, -1):
        if residual == ZERO:
            break
        if residual > ZERO:
            adjustment = min(residual, money(weights[index]) - result[index])
        else:
            adjustment = -min(-residual, result[index])
        result[index] = money(result[index] + adjustment)
        residual = money(residual - adjustment)
    if residual != ZERO or money(sum(result, ZERO)) != target:
        raise GiftCertificateError(
            "Gift Certificate funding split does not reconcile",
            "CERT_POSTING_INCOMPLETE",
        )
    return result
