from __future__ import annotations

import frappe

from erpnext_ua.ua_gift_certificates.adapters import pos as adapter
from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError
from erpnext_ua.ua_pos.api import _owned_order, get_session


@frappe.whitelist(methods=["POST"])
def quote_redemption(pos_session_token: str, order: str, token: str, requested_amount: str | None = None):
    session = get_session(pos_session_token)
    document = _owned_order(session, order)
    try:
        return adapter.quote(document, token, requested_amount)
    except GiftCertificateError as error:
        frappe.throw(str(error), title=error.code)


@frappe.whitelist(methods=["POST"])
def reserve_redemption(pos_session_token: str, order: str, quote_id: str, idempotency_key: str):
    session = get_session(pos_session_token)
    _owned_order(session, order)
    try:
        reservation = adapter.reserve(order, quote_id, idempotency_key, pos_session_token)
    except GiftCertificateError as error:
        frappe.throw(str(error), title=error.code)
    frappe.db.commit()
    return {"reservation": reservation.name, "order": frappe.get_doc("POS Order", order).as_dict()}


@frappe.whitelist(methods=["POST"])
def release_redemption(pos_session_token: str, reservation: str, idempotency_key: str):
    session = get_session(pos_session_token)
    document = frappe.get_doc("UA Gift Certificate Reservation", reservation)
    _owned_order(session, document.pos_order)
    from erpnext_ua.ua_gift_certificates.services.reservation import release_reservation

    try:
        result = release_reservation(reservation, reason="CASHIER_RELEASE", idempotency_key=idempotency_key)
    except GiftCertificateError as error:
        frappe.throw(str(error), title=error.code)
    frappe.db.commit()
    return result.as_dict()


@frappe.whitelist(methods=["POST"])
def add_certificate_sale_row(
    pos_session_token: str,
    order: str,
    program: str,
    face_value: str,
    idempotency_key: str,
    sale_price: str | None = None,
    holder_mode: str | None = None,
    holder_customer: str | None = None,
):
    session = get_session(pos_session_token)
    document = _owned_order(session, order)
    from erpnext_ua.ua_gift_certificates.services.sale import add_pos_sale_row

    try:
        certificate = add_pos_sale_row(
            document,
            program=program,
            face_value=face_value,
            sale_price=sale_price,
            holder_mode=holder_mode,
            holder_customer=holder_customer,
            idempotency_key=idempotency_key,
        )
    except GiftCertificateError as error:
        frappe.throw(str(error), title=error.code)
    frappe.db.commit()
    return {"certificate": certificate.public_serial, "order": document.reload().as_dict()}
