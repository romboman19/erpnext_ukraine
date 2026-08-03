from __future__ import annotations

import frappe

from erpnext_ua.ua_gift_certificates.context import service_write
from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError

REPRINT_ROLES = {
    "Gift Certificate Senior Cashier",
    "Gift Certificate Manager",
    "Gift Certificate Administrator",
    "System Manager",
}


def claim_sale_print_payload(pos_order: str, *, idempotency_key: str) -> dict:
    """Return plaintext tokens once, only for an authenticated protected print view."""
    frappe.db.sql("select name from `tabPOS Order` where name=%s for update", pos_order)
    order = frappe.get_doc("POS Order", pos_order)
    if order.order_purpose != "Gift Certificate Sale" or not order.gift_certificate_sale:
        raise GiftCertificateError("Order is not a completed certificate sale", "CERT_PRINT_NOT_AVAILABLE")
    if order.status not in {"Posted", "Completed", "Completed Print Error", "Printing"}:
        raise GiftCertificateError("Certificate sale is not completed", "CERT_PRINT_NOT_AVAILABLE")
    sale = frappe.get_doc("UA Gift Certificate Sale", order.gift_certificate_sale)
    payload = []
    for row in sale.certificates:
        payload.append(_claim_certificate(sale, order, row.certificate, idempotency_key))
    return {"sale": sale.name, "order": order.name, "certificates": payload}


def _claim_certificate(sale, order, certificate_name: str, request_key: str) -> dict:
    frappe.db.sql("select name from `tabUA Gift Certificate` where name=%s for update", certificate_name)
    certificate = frappe.get_doc("UA Gift Certificate", certificate_name)
    prior_count = frappe.db.count("UA Gift Certificate Print Grant", {"certificate": certificate.name})
    purpose = "Original" if prior_count == 0 else "Duplicate"
    program = frappe.get_doc("UA Gift Certificate Program", certificate.program)
    if purpose == "Duplicate":
        if program.get("print_token_once"):
            raise GiftCertificateError(
                "This program requires replacement instead of token reprint",
                "CERT_REPLACEMENT_REQUIRED",
            )
        if not REPRINT_ROLES.intersection(frappe.get_roles()):
            raise frappe.PermissionError("Gift Certificate duplicate print requires manager authorization")
    key = f"print:{sale.name}:{certificate.name}:{request_key}"
    if frappe.db.exists("UA Gift Certificate Print Grant", {"idempotency_key": key}):
        raise GiftCertificateError("Print grant has already been consumed", "CERT_PRINT_GRANT_CONSUMED")
    now = frappe.utils.now_datetime()
    with service_write():
        grant = frappe.get_doc(
            {
                "doctype": "UA Gift Certificate Print Grant",
                "certificate": certificate.name,
                "certificate_sale": sale.name,
                "pos_order": order.name,
                "purpose": purpose,
                "print_number": prior_count + 1,
                "issued_to": frappe.session.user,
                "approved_by": frappe.session.user if purpose == "Duplicate" else None,
                "issued_at": now,
                "expires_at": frappe.utils.add_to_date(now, minutes=2, as_datetime=True),
                "consumed_at": now,
                "idempotency_key": key,
            }
        ).insert(ignore_permissions=True)
    token = certificate.get_password("token_ciphertext")
    if not token:
        raise GiftCertificateError("Encrypted token is unavailable", "CERT_PRINT_NOT_AVAILABLE")
    return {
        "public_serial": certificate.public_serial,
        "token": token,
        "face_value": certificate.face_value,
        "valid_from": certificate.valid_from,
        "valid_until": certificate.valid_until,
        "holder_customer": certificate.holder_customer,
        "purpose": purpose,
        "print_number": grant.print_number,
    }
