from __future__ import annotations

import json

import frappe

from erpnext_ua.ua_gift_certificates.context import service_write
from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateConflict, GiftCertificateError
from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money

from .common import lock_certificate
from .ledger import append_entry
from .redemption import decode_quote
from .settings import require_enabled


def reserve_redemption(order_name: str, quote_id: str, *, idempotency_key: str, session_token_hash=None):
    require_enabled(pos_redemption=True)
    payload = decode_quote(quote_id)
    if payload["order"] != order_name:
        raise GiftCertificateConflict("Quote belongs to another order", "CERT_ORDER_CHANGED")
    existing = frappe.db.get_value("UA Gift Certificate Reservation", {"idempotency_key": idempotency_key}, "name")
    if existing:
        return frappe.get_doc("UA Gift Certificate Reservation", existing)
    frappe.db.sql("select name from `tabPOS Order` where name=%s for update", order_name)
    order = frappe.get_doc("POS Order", order_name)
    certificate = lock_certificate(payload["certificate"])
    if str(order.modified) != payload["order_modified"] or int(certificate.row_version or 0) != int(
        payload["certificate_row_version"]
    ):
        raise GiftCertificateError("Order or certificate changed after quote", "CERT_ORDER_CHANGED")
    requested = money(payload["amount"])
    if requested > money(certificate.available_balance):
        raise GiftCertificateError("Certificate balance is already reserved", "CERT_INSUFFICIENT_BALANCE")
    duplicate = frappe.db.exists(
        "UA Gift Certificate Reservation",
        {"certificate": certificate.name, "pos_order": order.name, "status": ("in", ["Active", "Consuming"])},
    )
    if duplicate:
        raise GiftCertificateError("Certificate is already reserved in this order", "CERT_ALREADY_RESERVED")
    ttl = int(frappe.db.get_single_value("UA Gift Certificate Settings", "reservation_ttl_seconds") or 600)
    created_at = frappe.utils.now_datetime()
    with service_write():
        reservation = frappe.get_doc(
            {
                "doctype": "UA Gift Certificate Reservation",
                "certificate": certificate.name,
                "pos_order": order.name,
                "requested_amount": requested,
                "paid_component_reserved": payload["paid"],
                "promotional_component_reserved": payload["promotional"],
                "eligible_total_snapshot": payload["eligible_total"],
                "status": "Active",
                "created_at": created_at,
                "expires_at": frappe.utils.add_to_date(created_at, seconds=ttl, as_datetime=True),
                "row_version_snapshot": int(certificate.row_version or 0),
                "idempotency_key": idempotency_key,
                "session_token_hash": session_token_hash,
                "cash_desk": order.cash_desk,
                "employee": order.employee,
                "policy_snapshot_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            }
        ).insert(ignore_permissions=True)
    append_entry(
        certificate,
        transaction_type="Reserve",
        reserved_delta=requested,
        idempotency_key=f"ledger:{idempotency_key}",
        reference_doctype="POS Order",
        reference_name=order.name,
        reason_code="CHECKOUT_RESERVATION",
        values={"reservation": reservation.name, "pos_order": order.name, "cash_desk": order.cash_desk},
    )
    order.reload()
    order.gift_certificate_redeemed_total = money(order.gift_certificate_redeemed_total) + requested
    order.gift_certificate_paid_component = money(order.gift_certificate_paid_component) + money(payload["paid"])
    order.gift_certificate_promotional_component = money(order.gift_certificate_promotional_component) + money(
        payload["promotional"]
    )
    order.gift_certificate_snapshot_json = json.dumps(
        {"reservations": active_reservation_snapshot(order.name)}, ensure_ascii=False, sort_keys=True
    )
    order.save(ignore_permissions=True)
    return reservation


def active_reservation_snapshot(order_name: str) -> list[dict]:
    return frappe.get_all(
        "UA Gift Certificate Reservation",
        filters={"pos_order": order_name, "status": ("in", ["Active", "Consuming"])},
        fields=["name", "certificate", "requested_amount", "paid_component_reserved", "promotional_component_reserved"],
        order_by="creation, name",
    )


def mark_consuming(order_name: str):
    reservations = active_reservation_snapshot(order_name)
    for row in reservations:
        frappe.db.sql("select name from `tabUA Gift Certificate Reservation` where name=%s for update", row.name)
        reservation = frappe.get_doc("UA Gift Certificate Reservation", row.name)
        if reservation.status == "Active":
            with service_write():
                reservation.status = "Consuming"
                reservation.save(ignore_permissions=True)
    return reservations


def release_reservation(reservation_name: str, *, reason: str, idempotency_key: str):
    frappe.db.sql("select name from `tabUA Gift Certificate Reservation` where name=%s for update", reservation_name)
    reservation = frappe.get_doc("UA Gift Certificate Reservation", reservation_name)
    certificate = lock_certificate(reservation.certificate)
    if reservation.status in {"Released", "Expired"}:
        return reservation
    if reservation.status == "Consumed":
        raise GiftCertificateError("Consumed reservation cannot be released", "CERT_POSTING_INCOMPLETE")
    append_entry(
        certificate,
        transaction_type="Release Reservation",
        reserved_delta=-money(reservation.requested_amount),
        idempotency_key=f"ledger:{idempotency_key}",
        reference_doctype="UA Gift Certificate Reservation",
        reference_name=reservation.name,
        reason_code=reason,
        values={"reservation": reservation.name, "pos_order": reservation.pos_order},
    )
    with service_write():
        reservation.status = "Expired" if reason == "TTL_EXPIRED" else "Released"
        reservation.reason = reason
        reservation.released_at = frappe.utils.now_datetime()
        reservation.save(ignore_permissions=True)
    _sync_order_totals(reservation.pos_order)
    return reservation


def _sync_order_totals(order_name: str) -> None:
    if not frappe.db.exists("POS Order", order_name):
        return
    rows = active_reservation_snapshot(order_name)
    total = money(sum((money(row.requested_amount) for row in rows), ZERO))
    paid = money(sum((money(row.paid_component_reserved) for row in rows), ZERO))
    promotional = money(sum((money(row.promotional_component_reserved) for row in rows), ZERO))
    order = frappe.get_doc("POS Order", order_name)
    order.gift_certificate_redeemed_total = total
    order.gift_certificate_paid_component = paid
    order.gift_certificate_promotional_component = promotional
    order.gift_certificate_snapshot_json = json.dumps(
        {"reservations": rows},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    order.gift_certificate_recovery_state = "Reserved" if rows else "None"
    order.save(ignore_permissions=True)


def release_stale_reservations():
    names = frappe.get_all(
        "UA Gift Certificate Reservation",
        filters={"status": "Active", "expires_at": ("<", frappe.utils.now_datetime())},
        pluck="name",
        limit=100,
    )
    for name in names:
        try:
            release_reservation(name, reason="TTL_EXPIRED", idempotency_key=f"expiry:{name}")
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(frappe.get_traceback(), f"Gift certificate reservation expiry {name}")
