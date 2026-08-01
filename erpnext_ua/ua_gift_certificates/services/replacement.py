from __future__ import annotations

import frappe
from frappe.model.naming import make_autoname

from erpnext_ua.ua_gift_certificates.context import service_write
from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError
from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money
from erpnext_ua.ua_gift_certificates.domain.token import generate_token, token_hash

from .common import hmac_secret, lock_certificate
from .ledger import append_entry


def replace_certificate(
    old_name: str,
    *,
    reason: str,
    idempotency_key: str,
    approved_by: str,
    allow_same_approver: bool = False,
):
    if not allow_same_approver and approved_by == frappe.session.user:
        raise GiftCertificateError("Replacement requires another approver", "CERT_DUAL_CONTROL_REQUIRED")
    existing = frappe.db.get_value(
        "UA Gift Certificate Replacement", {"idempotency_key": idempotency_key}, "new_certificate"
    )
    if existing:
        return frappe.get_doc("UA Gift Certificate", existing), None
    old = lock_certificate(old_name)
    if money(old.reserved_balance) > ZERO:
        raise GiftCertificateError("Certificate has an active reservation", "CERT_ALREADY_RESERVED")
    if old.status in {"Replaced", "Refunded", "Cancelled"}:
        raise GiftCertificateError("Certificate cannot be replaced", "CERT_REPLACED")
    material = generate_token()
    digest = token_hash(material.token, hmac_secret(old.token_key_version))
    with service_write():
        new = frappe.copy_doc(old)
        new.name = None
        new.public_serial = make_autoname("GC-.YYYY.-.######")
        new.token_hash = digest
        new.token_ciphertext = material.token
        new.token_last4 = material.last4
        new.checksum = material.checksum
        new.status = "Issued"
        new.current_balance = ZERO
        new.paid_balance = ZERO
        new.promotional_balance = ZERO
        new.reserved_balance = ZERO
        new.available_balance = ZERO
        new.redeemed_total = ZERO
        new.forfeited_total = ZERO
        new.expired_total = ZERO
        new.restored_total = ZERO
        new.replacement_of = old.name
        new.replaced_by = None
        new.blocked_reason = None
        new.blocked_by = None
        new.blocked_at = None
        new.insert(ignore_permissions=True)
    paid = money(old.paid_balance)
    promotional = money(old.promotional_balance)
    old_paid = (
        append_entry(
            old,
            transaction_type="Replace Out",
            paid_delta=-paid,
            idempotency_key=f"{idempotency_key}:out:paid",
            reference_doctype="UA Gift Certificate",
            reference_name=old.name,
            reason_code=reason,
        )
        if paid
        else None
    )
    old.reload()
    old_promotional = (
        append_entry(
            old,
            transaction_type="Replace Out",
            promotional_delta=-promotional,
            idempotency_key=f"{idempotency_key}:out:promotional",
            reference_doctype="UA Gift Certificate",
            reference_name=old.name,
            reason_code=reason,
        )
        if promotional
        else None
    )
    new.reload()
    new_paid = (
        append_entry(
            new,
            transaction_type="Replace In",
            paid_delta=paid,
            idempotency_key=f"{idempotency_key}:in:paid",
            reference_doctype="UA Gift Certificate",
            reference_name=new.name,
            reason_code=reason,
        )
        if paid
        else None
    )
    new.reload()
    new_promotional = (
        append_entry(
            new,
            transaction_type="Replace In",
            promotional_delta=promotional,
            idempotency_key=f"{idempotency_key}:in:promotional",
            reference_doctype="UA Gift Certificate",
            reference_name=new.name,
            reason_code=reason,
        )
        if promotional
        else None
    )
    with service_write():
        old.reload()
        old.status = "Replaced"
        old.replaced_by = new.name
        old.save(ignore_permissions=True)
        new.reload()
        new.status = "Active"
        new.save(ignore_permissions=True)
    with service_write():
        frappe.get_doc(
            {
                "doctype": "UA Gift Certificate Replacement",
                "old_certificate": old.name,
                "new_certificate": new.name,
                "reason": reason,
                "balance_transferred": money(paid + promotional),
                "paid_transferred": paid,
                "promotional_transferred": promotional,
                "requested_by": frappe.session.user,
                "approved_by": approved_by,
                "identity_verified": 1 if reason == "Return Restore" else 0,
                "status": "Executed",
                "idempotency_key": idempotency_key,
                "old_ledger_entry": (old_paid or old_promotional).name if (old_paid or old_promotional) else None,
                "new_ledger_entry": (new_paid or new_promotional).name if (new_paid or new_promotional) else None,
            }
        ).insert(ignore_permissions=True)
    return new, material.token
