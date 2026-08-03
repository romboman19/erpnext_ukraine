from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

import frappe

from erpnext_ua.ua_gift_certificates.context import service_write
from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateConflict, GiftCertificateError
from erpnext_ua.ua_gift_certificates.domain.lifecycle import status_after_balance
from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money

from .common import canonical_json, lock_certificate, payload_hash


def append_entry(
    certificate,
    *,
    transaction_type: str,
    paid_delta: Decimal = ZERO,
    promotional_delta: Decimal = ZERO,
    reserved_delta: Decimal = ZERO,
    idempotency_key: str,
    reference_doctype: str,
    reference_name: str,
    reason_code: str,
    values: dict[str, Any] | None = None,
):
    values = values or {}
    certificate = lock_certificate(certificate.name if hasattr(certificate, "name") else certificate)
    paid_delta = money(paid_delta)
    promotional_delta = money(promotional_delta)
    reserved_delta = money(reserved_delta)
    balance_delta = money(paid_delta + promotional_delta)
    payload = {
        "certificate": certificate.name,
        "transaction_type": transaction_type,
        "paid_delta": str(paid_delta),
        "promotional_delta": str(promotional_delta),
        "reserved_delta": str(reserved_delta),
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "reason_code": reason_code,
        "reversal_of": values.get("reversal_of"),
    }
    fingerprint = payload_hash(payload)
    existing = frappe.db.get_value(
        "UA Gift Certificate Ledger Entry",
        {"idempotency_key": idempotency_key},
        ["name", "payload_hash"],
        as_dict=True,
    )
    if existing:
        if existing.payload_hash != fingerprint:
            raise GiftCertificateConflict("Idempotency key has another payload", "CERT_POSTING_INCOMPLETE")
        return frappe.get_doc("UA Gift Certificate Ledger Entry", existing.name)

    before = money(certificate.current_balance)
    paid_before = money(certificate.paid_balance)
    promotional_before = money(certificate.promotional_balance)
    after = money(before + balance_delta)
    paid_after = money(paid_before + paid_delta)
    promotional_after = money(promotional_before + promotional_delta)
    reserved_after = money(certificate.reserved_balance) + reserved_delta
    if after < ZERO or paid_after < ZERO or promotional_after < ZERO or reserved_after < ZERO or reserved_after > after:
        raise GiftCertificateError("Ledger delta would create an invalid balance", "CERT_POSTING_INCOMPLETE")
    previous_hash = (
        frappe.db.get_value(
            "UA Gift Certificate Ledger Entry",
            {"certificate": certificate.name},
            "created_hash",
            order_by="posting_datetime desc, creation desc",
        )
        or ""
    )
    created_hash = hashlib.sha256((canonical_json(payload) + previous_hash).encode("utf-8")).hexdigest()
    document = {
        "doctype": "UA Gift Certificate Ledger Entry",
        "certificate": certificate.name,
        "posting_datetime": values.get("posting_datetime") or frappe.utils.now_datetime(),
        "effective_date": values.get("effective_date") or frappe.utils.today(),
        "transaction_type": transaction_type,
        "balance_delta": balance_delta,
        "paid_delta": paid_delta,
        "promotional_delta": promotional_delta,
        "reserved_delta": reserved_delta,
        "liability_delta": money(values.get("liability_delta")),
        "settlement_delta": money(values.get("settlement_delta")),
        "balance_before": before,
        "balance_after": after,
        "paid_before": paid_before,
        "paid_after": paid_after,
        "promotional_before": promotional_before,
        "promotional_after": promotional_after,
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "idempotency_key": idempotency_key,
        "payload_hash": fingerprint,
        "reason_code": reason_code,
        "created_hash": created_hash,
        "previous_hash": previous_hash,
        "user": frappe.session.user,
    }
    for fieldname in (
        "issuer_company",
        "issuer_fop_profile",
        "redeemer_company",
        "redeemer_fop_profile",
        "cash_desk",
        "operational_shift",
        "employee",
        "reference_row",
        "pos_order",
        "sales_invoice",
        "prro_receipt",
        "reservation",
        "reversal_of",
        "policy_snapshot_checksum",
        "notes",
    ):
        if values.get(fieldname) is not None:
            document[fieldname] = values[fieldname]
    with service_write():
        entry = frappe.get_doc(document).insert(ignore_permissions=True)
        certificate.current_balance = after
        certificate.paid_balance = paid_after
        certificate.promotional_balance = promotional_after
        certificate.reserved_balance = reserved_after
        certificate.available_balance = (
            ZERO
            if certificate.status in {"Blocked", "Expired", "Replaced", "Refunded", "Cancelled"}
            else money(after - reserved_after)
        )
        certificate.row_version = int(certificate.row_version or 0) + 1
        if transaction_type.startswith("Redeem"):
            certificate.redeemed_total = money(certificate.redeemed_total) - balance_delta
        elif transaction_type.startswith("Restore"):
            certificate.restored_total = money(certificate.restored_total) + balance_delta
        elif transaction_type.startswith("Forfeit"):
            certificate.forfeited_total = money(certificate.forfeited_total) - balance_delta
        if certificate.status not in {"Blocked", "Expired", "Replaced", "Refunded", "Cancelled"}:
            program = frappe.db.get_value("UA Gift Certificate Program", certificate.program, "usage_policy")
            certificate.status = (
                status_after_balance(
                    balance=certificate.current_balance,
                    redeemed_total=certificate.redeemed_total,
                    usage_policy=program,
                )
                if (after > ZERO or money(certificate.redeemed_total) > ZERO)
                else certificate.status
            )
        certificate.save(ignore_permissions=True)
    return entry
