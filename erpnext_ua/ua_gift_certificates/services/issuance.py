from __future__ import annotations

from dataclasses import asdict

import frappe
from frappe.model.naming import make_autoname

from erpnext_ua.ua_gift_certificates.context import service_write
from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError
from erpnext_ua.ua_gift_certificates.domain.funding import initial_funding
from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money
from erpnext_ua.ua_gift_certificates.domain.token import generate_token, token_hash

from .common import canonical_json, hmac_secret, payload_hash
from .compliance import resolve_profile
from .ledger import append_entry
from .settings import require_enabled, settings


def issue_certificate(
    *,
    program_name: str,
    face_value,
    sale_price=None,
    holder_mode: str | None = None,
    holder_customer: str | None = None,
    buyer_customer: str | None = None,
    issuer_company: str,
    issuer_fop_profile: str | None = None,
    issuer_cash_desk: str | None = None,
    pos_order: str | None = None,
    batch: str | None = None,
    idempotency_key: str,
):
    require_enabled()
    existing = frappe.db.get_value(
        "UA Gift Certificate Ledger Entry", {"idempotency_key": idempotency_key}, "certificate"
    )
    if existing:
        return frappe.get_doc("UA Gift Certificate", existing), None
    program = frappe.get_doc("UA Gift Certificate Program", program_name)
    if program.status != "Active":
        raise GiftCertificateError("Program is not active", "CERT_COMPLIANCE_DENIED")
    face = money(face_value)
    price = money(face if sale_price in (None, "") else sale_price)
    _validate_denomination(program, face, price)
    mode = holder_mode or ("Bearer" if program.holder_mode == "Buyer Chooses" else program.holder_mode)
    if mode == "Named" and not holder_customer:
        raise GiftCertificateError("Named certificate requires a holder", "CERT_HOLDER_MISMATCH")
    compliance, decision = resolve_profile(
        company=issuer_company,
        fop_profile=issuer_fop_profile,
        profile_name=program.compliance_profile,
        action="issue",
    )
    material = generate_token()
    config = settings()
    version = config.token_hmac_key_version or "v1"
    digest = token_hash(material.token, hmac_secret(version))
    funding = initial_funding(face, price)
    snapshot = {
        "program": program.name,
        "program_version": int(program.version),
        "network": program.network,
        "accounting_model": program.accounting_model,
        "usage_policy": program.usage_policy,
        "under_spend_policy": program.under_spend_policy,
        "funding_consumption_policy": program.funding_consumption_policy,
        "holder_mode": mode,
        "restore_mode": program.restore_mode,
        "restored_validity_policy": program.restored_validity_policy,
        "restored_validity_days": int(program.restored_validity_days or 0),
        "validity_days": int(program.validity_days or 0),
        "accounting_profile": program.accounting_profile,
        "compliance_profile": compliance.name,
        "compliance_version": decision.version,
        "funding": {key: str(value) for key, value in asdict(funding).items()},
    }
    checksum = payload_hash(snapshot)
    with service_write():
        certificate = frappe.get_doc(
            {
                "doctype": "UA Gift Certificate",
                "public_serial": make_autoname("GC-.YYYY.-.######"),
                "token_hash": digest,
                "token_ciphertext": material.token if config.allow_encrypted_token_storage else None,
                "token_last4": material.last4,
                "token_key_version": version,
                "token_format_version": "GC1",
                "checksum": material.checksum,
                "network": program.network,
                "program": program.name,
                "program_version": int(program.version),
                "batch": batch,
                "status": "Issued",
                "holder_mode": mode,
                "holder_customer": holder_customer,
                "buyer_customer": buyer_customer,
                "currency": "UAH",
                "face_value": face,
                "sale_price": price,
                "initial_paid_funding": funding.paid,
                "initial_promotional_funding": funding.promotional,
                "premium_fee": funding.premium,
                "current_balance": ZERO,
                "paid_balance": ZERO,
                "promotional_balance": ZERO,
                "reserved_balance": ZERO,
                "available_balance": ZERO,
                "issue_date": frappe.utils.today(),
                "issuer_company": issuer_company,
                "issuer_fop_profile": issuer_fop_profile,
                "issuer_cash_desk": issuer_cash_desk,
                "issue_pos_order": pos_order,
                "policy_snapshot_json": canonical_json(snapshot),
                "policy_checksum": checksum,
                "accounting_profile_version": program.accounting_profile,
                "compliance_profile_version": f"{compliance.name}:{decision.version}",
            }
        ).insert(ignore_permissions=True)
    append_entry(
        certificate,
        transaction_type="Issue",
        idempotency_key=idempotency_key,
        reference_doctype="POS Order" if pos_order else "UA Gift Certificate",
        reference_name=pos_order or certificate.name,
        reason_code="ISSUED",
        values={
            "issuer_company": issuer_company,
            "issuer_fop_profile": issuer_fop_profile,
            "cash_desk": issuer_cash_desk,
        },
    )
    return certificate, material.token


def activate_certificate(
    certificate_name: str,
    *,
    sale_reference: str,
    payment_evidence: str,
    idempotency_key: str,
    activation_date=None,
):
    if not payment_evidence:
        raise GiftCertificateError("Confirmed payment evidence is required", "CERT_EXTERNAL_PAYMENT_PENDING")
    frappe.db.sql("select name from `tabUA Gift Certificate` where name=%s for update", certificate_name)
    certificate = frappe.get_doc("UA Gift Certificate", certificate_name)
    if (
        certificate.status in {"Active", "Partially Redeemed", "Fully Redeemed"}
        and certificate.activation_date
        and certificate.certificate_sale
    ):
        return certificate
    if certificate.status not in {"Issued", "Reserved For Sale", "Payment Pending", "Active"}:
        raise GiftCertificateError("Certificate cannot be activated", "CERT_POSTING_INCOMPLETE")
    program = frappe.get_doc("UA Gift Certificate Program", certificate.program)
    resolve_profile(
        company=certificate.issuer_company,
        fop_profile=certificate.issuer_fop_profile,
        profile_name=program.compliance_profile,
        action="sale",
    )
    paid_entry = append_entry(
        certificate,
        transaction_type="Activate Paid",
        paid_delta=certificate.initial_paid_funding,
        idempotency_key=f"{idempotency_key}:paid",
        reference_doctype="UA Gift Certificate Sale",
        reference_name=sale_reference,
        reason_code="PAID_ACTIVATION",
        values={"liability_delta": certificate.initial_paid_funding, "issuer_company": certificate.issuer_company},
    )
    certificate.reload()
    promotional_entry = append_entry(
        certificate,
        transaction_type="Activate Promotional",
        promotional_delta=certificate.initial_promotional_funding,
        idempotency_key=f"{idempotency_key}:promotional",
        reference_doctype="UA Gift Certificate Sale",
        reference_name=sale_reference,
        reason_code="PROMOTIONAL_ACTIVATION",
        values={"issuer_company": certificate.issuer_company},
    )
    certificate.reload()
    activated_at = activation_date or frappe.utils.now_datetime()
    valid_until = frappe.utils.add_days(frappe.utils.getdate(activated_at), int(program.validity_days or 0))
    with service_write():
        certificate.status = "Active"
        certificate.activation_date = activated_at
        certificate.sale_date = frappe.utils.getdate(activated_at)
        certificate.valid_from = frappe.utils.getdate(activated_at)
        certificate.valid_until = valid_until
        certificate.certificate_sale = sale_reference
        certificate.row_version = int(certificate.row_version or 0) + 1
        certificate.save(ignore_permissions=True)
    return certificate, paid_entry, promotional_entry


def _validate_denomination(program, face, price):
    if face <= ZERO or face < money(program.min_face_value) or face > money(program.max_face_value):
        raise GiftCertificateError("Face value is outside Program limits", "CERT_REDEMPTION_LIMIT")
    step = money(program.denomination_step or 1)
    if program.denomination_mode == "Variable" and step > ZERO and (face - money(program.min_face_value)) % step:
        raise GiftCertificateError("Face value does not match denomination step", "CERT_REDEMPTION_LIMIT")
    if price < face and not program.allow_discounted_sale:
        raise GiftCertificateError("Discounted certificate sale is not allowed", "CERT_COMPLIANCE_DENIED")
    if price > face and not program.allow_premium_sale:
        raise GiftCertificateError("Premium certificate sale is not allowed", "CERT_COMPLIANCE_DENIED")
