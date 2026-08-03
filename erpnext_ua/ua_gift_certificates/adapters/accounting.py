from __future__ import annotations

import json
from collections import defaultdict

import frappe

from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError
from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money


def invoice_payments(order, *, is_return: bool) -> list[dict]:
    result = []
    for row in order.payments_plan:
        if row.status != "Confirmed":
            continue
        sign = -1 if is_return else 1
        if row.kind != "Gift Certificate":
            result.append({"mode_of_payment": row.mode_of_payment, "amount": sign * abs(row.amount)})
            continue
        result.extend(_certificate_payment_components(order, row, sign))
    return result


def _certificate_payment_components(order, row, sign: int):
    desk_company = frappe.db.get_value("POS Cash Desk", order.cash_desk, "company")
    certificate = frappe.get_doc("UA Gift Certificate", row.gift_certificate)
    totals = defaultdict(lambda: ZERO)
    for allocation in _component_slices(order, row):
        redeemer_fop = allocation["redeemer_fop"]
        same_entity = certificate.issuer_company == desk_company and (
            (certificate.issuer_fop_profile or None) == (redeemer_fop or None)
        )
        if same_entity:
            profile_name = frappe.db.get_value(
                "UA Gift Certificate Program",
                certificate.program,
                "accounting_profile",
            )
            profile = _profile(profile_name)
            if allocation["paid"] > ZERO:
                mode = _mode(profile, "Paid Liability", profile.paid_liability_account)
                totals[mode] = money(totals[mode] + allocation["paid"])
            if allocation["promotional"] > ZERO:
                mode = _mode(profile, "Promotional", profile.promotional_expense_account)
                totals[mode] = money(totals[mode] + allocation["promotional"])
            continue
        profile = _redeemer_profile(certificate, desk_company, redeemer_fop)
        mode = _mode(profile, "Settlement Receivable", profile.settlement_receivable_account)
        totals[mode] = money(totals[mode] + allocation["paid"] + allocation["promotional"])
    components = [
        {"mode_of_payment": mode, "amount": sign * amount}
        for mode, amount in sorted(totals.items())
        if amount > ZERO
    ]
    if money(sum((money(component["amount"]) for component in components), ZERO)) != sign * money(row.amount):
        raise GiftCertificateError("Payment components do not match visible amount", "CERT_ACCOUNTING_PROFILE_INVALID")
    return components


def _component_slices(order, row) -> list[dict]:
    if order.order_type == "Return":
        allocation = frappe.get_doc(
            "UA Gift Certificate Redemption Allocation",
            row.gift_certificate_redemption_allocation,
        )
        return [
            {
                "paid": money(row.gift_certificate_paid_amount),
                "promotional": money(row.gift_certificate_promotional_amount),
                "redeemer_fop": allocation.redeemer_fop_profile,
            }
        ]
    reservation = frappe.get_doc("UA Gift Certificate Reservation", row.gift_certificate_reservation)
    payload = json.loads(reservation.policy_snapshot_json)
    total = money(reservation.requested_amount)
    paid_total = money(reservation.paid_component_reserved)
    paid_allocated = ZERO
    result = []
    allocations = payload["allocations"]
    for index, allocation in enumerate(allocations):
        amount = money(allocation["amount"])
        paid = (
            money(paid_total - paid_allocated)
            if index == len(allocations) - 1
            else min(amount, money(amount * paid_total / total))
        )
        paid_allocated = money(paid_allocated + paid)
        redeemer_fop = frappe.db.get_value("POS Order Item", allocation["row"], "fop_profile")
        result.append(
            {
                "paid": paid,
                "promotional": money(amount - paid),
                "redeemer_fop": redeemer_fop,
            }
        )
    return result


def _profile(name: str | None):
    if not name:
        raise GiftCertificateError("Accounting Profile is missing", "CERT_ACCOUNTING_PROFILE_INVALID")
    return frappe.get_doc("UA Gift Certificate Accounting Profile", name)


def _redeemer_profile(certificate, company: str, fop_profile: str | None):
    filters = {
        "parent": certificate.network,
        "company": company,
        "entity_role": ("in", ["Redeemer", "Both"]),
    }
    filters["fop_profile"] = fop_profile or ("is", "not set")
    name = frappe.db.get_value("UA Gift Certificate Network Entity", filters, "accounting_profile")
    return _profile(name)


def _mode(profile, component: str, expected_account: str) -> str:
    mode = frappe.db.get_value(
        "Mode of Payment",
        {
            "ua_gift_certificate_accounting_profile": profile.name,
            "ua_gift_certificate_component": component,
            "enabled": 1,
        },
        "name",
    )
    if not mode:
        raise GiftCertificateError(f"Mode of Payment for {component} is missing", "CERT_ACCOUNTING_PROFILE_INVALID")
    account = frappe.db.get_value(
        "Mode of Payment Account", {"parent": mode, "company": profile.company}, "default_account"
    )
    if account != expected_account:
        raise GiftCertificateError(f"Mode of Payment {mode} has an invalid account", "CERT_ACCOUNTING_PROFILE_INVALID")
    return mode
