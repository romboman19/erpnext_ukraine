from __future__ import annotations

import frappe

from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError
from erpnext_ua.ua_gift_certificates.domain.money import canonical
from erpnext_ua.ua_gift_certificates.domain.token import masked
from erpnext_ua.ua_gift_certificates.services.redemption import certificate_by_token


@frappe.whitelist(methods=["POST"])
def certificate_status_by_token(token: str):
    frappe.only_for(("Gift Certificate Cashier", "Gift Certificate Manager", "Gift Certificate Auditor"))
    try:
        certificate = certificate_by_token(token)
    except GiftCertificateError as error:
        frappe.throw(str(error), title=error.code)
    return {
        "public_serial": certificate.public_serial,
        "masked_token": masked(certificate.token_last4),
        "status": certificate.status,
        "available_balance": canonical(certificate.available_balance),
        "valid_until": certificate.valid_until,
        "network": certificate.network,
    }
