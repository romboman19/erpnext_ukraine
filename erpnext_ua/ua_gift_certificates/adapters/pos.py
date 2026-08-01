from __future__ import annotations

import hashlib
import json

import frappe

from erpnext_ua.ua_gift_certificates.domain.allocation import restore_share
from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError
from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money

from ..services.redemption import quote_redemption
from ..services.reservation import (
    active_reservation_snapshot,
    mark_consuming,
    release_reservation,
    reserve_redemption,
)
from ..services.settings import enabled_for_pos_redemption


def quote(order, token: str, requested_amount=None):
    return quote_redemption(order, token, requested_amount)


def reserve(order: str, quote_id: str, idempotency_key: str, session_token: str):
    session_hash = hashlib.sha256(session_token.encode()).hexdigest()
    return reserve_redemption(order, quote_id, idempotency_key=idempotency_key, session_token_hash=session_hash)


def checkout_payment_rows(order) -> list[dict]:
    if not enabled_for_pos_redemption():
        return []
    if order.order_type == "Return":
        return return_payment_rows(order)
    rows = []
    for reservation in active_reservation_snapshot(order.name):
        certificate = frappe.get_doc("UA Gift Certificate", reservation.certificate)
        rows.append(
            {
                "mode_of_payment": _visible_mode_of_payment(certificate.network),
                "kind": "Gift Certificate",
                "prro_payment_form": "БЕЗГОТІВКОВА",
                "prro_payment_means": "Подарунковий сертифікат",
                "prro_payment_code": 100000,
                "payment_context": "Звичайна оплата",
                "amount": money(reservation.requested_amount),
                "tendered_amount": money(reservation.requested_amount),
                "change_amount": ZERO,
                "currency": "UAH",
                "gift_certificate": certificate.name,
                "gift_certificate_reservation": reservation.name,
                "gift_certificate_public_serial": certificate.public_serial,
                "gift_certificate_paid_amount": money(reservation.paid_component_reserved),
                "gift_certificate_promotional_amount": money(reservation.promotional_component_reserved),
                "gift_certificate_allocation_group": f"{order.name}:{reservation.name}",
            }
        )
    return rows


def prepare_checkout(order, idem_key: str) -> list[dict]:
    if order.get("order_purpose") == "Gift Certificate Sale":
        from ..services.sale import prepare_payment

        prepare_payment(order)
        frappe.db.commit()
        return []
    rows = checkout_payment_rows(order)
    if not rows:
        return []
    if order.order_type == "Sale":
        mark_consuming(order.name)
    order.gift_certificate_recovery_state = "Payment In Progress"
    order.save(ignore_permissions=True)
    # Required durable boundary: reservations are committed before a terminal call.
    frappe.db.commit()
    return rows


def release_checkout(order, reason="PAYMENT_FAILED"):
    for row in active_reservation_snapshot(order.name):
        release_reservation(row.name, reason=reason, idempotency_key=f"{reason.lower()}:{order.name}:{row.name}")


def prepare_return_order(return_order, original_order):
    components = _planned_return_components(return_order, original_order)
    total = money(sum((money(row["amount"]) for row in components), ZERO))
    paid = money(sum((money(row["paid"]) for row in components), ZERO))
    promotional = money(sum((money(row["promotional"]) for row in components), ZERO))
    return_order.gift_certificate_redeemed_total = money(total)
    return_order.gift_certificate_paid_component = money(paid)
    return_order.gift_certificate_promotional_component = money(promotional)
    return_order.gift_certificate_snapshot_json = json.dumps(
        {
            "return_against": original_order.name,
            "restore_total": str(money(total)),
            "components": components,
        },
        sort_keys=True,
    )


def return_payment_rows(order) -> list[dict]:
    amount = money(order.gift_certificate_redeemed_total)
    if amount <= ZERO:
        return []
    snapshot = json.loads(order.gift_certificate_snapshot_json or "{}")
    rows = []
    for component in snapshot.get("components", []):
        certificate = frappe.get_doc("UA Gift Certificate", component["certificate"])
        rows.append(
            {
                "mode_of_payment": _visible_mode_of_payment(certificate.network),
                "kind": "Gift Certificate",
                "prro_payment_form": "БЕЗГОТІВКОВА",
                "prro_payment_means": "Подарунковий сертифікат",
                "prro_payment_code": 100000,
                "payment_context": "Звичайна оплата",
                "amount": money(component["amount"]),
                "tendered_amount": money(component["amount"]),
                "change_amount": ZERO,
                "currency": "UAH",
                "gift_certificate": certificate.name,
                "gift_certificate_redemption_allocation": component["allocation"],
                "gift_certificate_redeemer_fop": component["redeemer_fop_profile"],
                "gift_certificate_public_serial": certificate.public_serial,
                "gift_certificate_paid_amount": money(component["paid"]),
                "gift_certificate_promotional_amount": money(component["promotional"]),
                "gift_certificate_allocation_group": f"return:{order.name}:{component['allocation']}",
            }
        )
    if money(sum((money(row["amount"]) for row in rows), ZERO)) != amount:
        raise GiftCertificateError("Return certificate components are incomplete", "CERT_POSTING_INCOMPLETE")
    return rows


def _planned_return_components(return_order, original_order) -> list[dict]:
    result = []
    for return_row in return_order.items:
        original_row = return_row.return_against_item
        original_qty = frappe.db.get_value("POS Order Item", original_row, "qty") or 0
        if not original_qty:
            continue
        allocations = frappe.get_all(
            "UA Gift Certificate Redemption Allocation",
            filters={"pos_order": original_order.name, "pos_order_item": original_row},
            fields=[
                "name",
                "certificate",
                "certificate_amount",
                "paid_component_amount",
                "promotional_component_amount",
                "issuer_fop_profile",
                "redeemer_fop_profile",
            ],
        )
        for allocation in allocations:
            prior = frappe.db.sql(
                """select coalesce(sum(certificate_amount_to_restore), 0) as amount,
                          coalesce(sum(paid_amount_to_restore), 0) as paid,
                          coalesce(sum(qty_returned), 0) as qty
                   from `tabUA Gift Certificate Return Allocation`
                   where original_redemption_allocation=%s""",
                allocation.name,
                as_dict=True,
            )[0]
            restored = restore_share(
                allocation.certificate_amount,
                return_row.qty,
                original_qty,
                prior.amount,
                prior.qty,
            )
            paid_remaining = money(allocation.paid_component_amount) - money(prior.paid)
            total_remaining = money(allocation.certificate_amount) - money(prior.amount)
            paid = (
                paid_remaining
                if restored == total_remaining
                else min(
                    paid_remaining,
                    money(restored * money(allocation.paid_component_amount) / money(allocation.certificate_amount)),
                )
            )
            result.append(
                {
                    "allocation": allocation.name,
                    "certificate": allocation.certificate,
                    "amount": str(restored),
                    "paid": str(paid),
                    "promotional": str(money(restored - paid)),
                    "issuer_fop_profile": allocation.issuer_fop_profile,
                    "redeemer_fop_profile": allocation.redeemer_fop_profile,
                }
            )
    return result


def _visible_mode_of_payment(network: str | None) -> str:
    filters = {"ua_gift_certificate_component": "Paid Liability", "enabled": 1}
    if network:
        filters["ua_gift_certificate_network"] = network
    name = frappe.db.get_value("Mode of Payment", filters, "name")
    if not name:
        raise GiftCertificateError(
            "Gift Certificate Mode of Payment is not configured", "CERT_ACCOUNTING_PROFILE_INVALID"
        )
    return name
