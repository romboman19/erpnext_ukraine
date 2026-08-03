from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

import frappe

from erpnext_ua.ua_gift_certificates.domain.allocation import EligibleLine, allocate_proportionally
from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError
from erpnext_ua.ua_gift_certificates.domain.funding import split_consumption
from erpnext_ua.ua_gift_certificates.domain.lifecycle import ensure_redeemable
from erpnext_ua.ua_gift_certificates.domain.money import ZERO, canonical, decimal, money
from erpnext_ua.ua_gift_certificates.domain.token import masked, token_hash, validate_token

from .common import canonical_json, hmac_secret
from .compliance import resolve_profile
from .settings import require_enabled

QUOTE_TTL_SECONDS = 180


def certificate_by_token(token: str):
    try:
        normalized = validate_token(token)
    except ValueError as exc:
        raise GiftCertificateError("Certificate is not valid", "CERT_NOT_FOUND") from exc
    version = frappe.db.get_single_value("UA Gift Certificate Settings", "token_hmac_key_version") or "v1"
    digest = token_hash(normalized, hmac_secret(version))
    name = frappe.db.get_value("UA Gift Certificate", {"token_hash": digest}, "name")
    if not name:
        _audit_failed_token(digest, "NOT_FOUND")
        raise GiftCertificateError("Certificate is not valid", "CERT_NOT_FOUND")
    return frappe.get_doc("UA Gift Certificate", name)


def quote_redemption(order, token: str, requested_amount=None) -> dict:
    require_enabled(pos_redemption=True)
    order = frappe.get_doc("POS Order", order) if isinstance(order, str) else order
    if order.order_type != "Sale" or order.get("order_purpose") == "Gift Certificate Sale":
        raise GiftCertificateError("A certificate cannot buy a certificate", "CERT_PURCHASE_WITH_CERTIFICATE_DENIED")
    if order.status not in {"Building", "Awaiting Payment"}:
        raise GiftCertificateError("Order cannot be changed", "CERT_ORDER_CHANGED")
    certificate = certificate_by_token(token)
    ensure_redeemable(certificate.status)
    _validate_certificate_context(certificate, order)
    program = frappe.get_doc("UA Gift Certificate Program", certificate.program)
    active_reservations = frappe.db.count(
        "UA Gift Certificate Reservation",
        {"pos_order": order.name, "status": ("in", ["Active", "Consuming"])},
    )
    if active_reservations and not program.allow_multiple_certificates_per_order:
        raise GiftCertificateError("Program allows one certificate per order", "CERT_REDEMPTION_LIMIT")
    if active_reservations >= int(program.max_certificates_per_order or 1):
        raise GiftCertificateError("Certificate count limit is reached", "CERT_REDEMPTION_LIMIT")
    lines, ineligible = _eligible_lines(program, order)
    eligible_total = money(sum((line.amount for line in lines), ZERO))
    if eligible_total <= ZERO:
        raise GiftCertificateError("Order has no eligible items", "CERT_NO_ELIGIBLE_ITEMS")
    maximum = min(eligible_total, money(certificate.available_balance))
    percent_cap = money(eligible_total * decimal(program.max_redemption_percent_of_eligible_total or 100) / 100)
    maximum = min(maximum, percent_cap)
    if money(program.max_redemption_amount) > ZERO:
        maximum = min(maximum, money(program.max_redemption_amount))
    amount = maximum if requested_amount in (None, "", "0", 0) else money(requested_amount)
    if amount <= ZERO or amount > maximum:
        raise GiftCertificateError("Requested amount exceeds redemption limit", "CERT_REDEMPTION_LIMIT")
    if amount < money(program.min_redemption_amount):
        raise GiftCertificateError("Requested amount is below redemption minimum", "CERT_REDEMPTION_LIMIT")
    forfeited = ZERO
    if program.usage_policy == "Single Use No Change" and amount < money(certificate.current_balance):
        if program.under_spend_policy == "Reject Underspend":
            raise GiftCertificateError("Under-spend is not allowed", "CERT_UNDERSPEND_NOT_ALLOWED")
        if program.under_spend_policy == "Forfeit Remainder":
            forfeited = money(certificate.current_balance) - amount
    component = split_consumption(
        certificate.paid_balance,
        certificate.promotional_balance,
        amount,
        program.funding_consumption_policy,
    )
    allocations = allocate_proportionally(amount, lines)
    expires_at = frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=QUOTE_TTL_SECONDS, as_datetime=True)
    payload = {
        "certificate": certificate.name,
        "certificate_row_version": int(certificate.row_version or 0),
        "order": order.name,
        "order_modified": str(order.modified),
        "amount": canonical(amount),
        "paid": canonical(component.paid),
        "promotional": canonical(component.promotional),
        "forfeited": canonical(forfeited),
        "eligible_total": canonical(eligible_total),
        "allocations": [{"row": row.row_name, "amount": canonical(row.amount)} for row in allocations],
        "expires_at": expires_at.isoformat(),
        "policy_checksum": certificate.policy_checksum,
    }
    return {
        "quote_id": _sign_quote(payload),
        "certificate": {
            "public_serial": certificate.public_serial,
            "masked_token": masked(certificate.token_last4),
            "available_balance": canonical(certificate.available_balance),
            "valid_until": str(certificate.valid_until or ""),
        },
        "eligible_total": canonical(eligible_total),
        "amount_to_redeem": canonical(amount),
        "paid_component": canonical(component.paid),
        "promotional_component": canonical(component.promotional),
        "balance_after": canonical(money(certificate.current_balance) - amount - forfeited),
        "forfeited_amount": canonical(forfeited),
        "eligible_rows": payload["allocations"],
        "ineligible_rows": ineligible,
        "expires_at": expires_at,
    }


def decode_quote(quote_id: str) -> dict:
    try:
        encoded, signature = quote_id.split(".", 1)
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        expected = hmac.new(hmac_secret().encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise GiftCertificateError("Redemption quote is invalid", "CERT_ORDER_CHANGED") from exc
    expires_at = datetime.fromisoformat(payload["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(UTC):
        raise GiftCertificateError("Redemption quote has expired", "CERT_RESERVATION_EXPIRED")
    return payload


def _sign_quote(payload: dict) -> str:
    raw = canonical_json(payload).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(hmac_secret().encode(), raw, hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _validate_certificate_context(certificate, order):
    today = frappe.utils.getdate()
    if certificate.valid_from and today < frappe.utils.getdate(certificate.valid_from):
        raise GiftCertificateError("Certificate is not activated", "CERT_NOT_ACTIVATED")
    if certificate.valid_until and today > frappe.utils.getdate(certificate.valid_until):
        raise GiftCertificateError("Certificate has expired", "CERT_EXPIRED")
    if certificate.holder_mode == "Named" and certificate.holder_customer != order.customer:
        raise GiftCertificateError("Certificate belongs to another customer", "CERT_HOLDER_MISMATCH")
    desk = frappe.get_doc("POS Cash Desk", order.cash_desk)
    location = frappe.db.get_value(
        "UA Gift Certificate Network Location",
        {"parent": certificate.network, "location_type": "POS Cash Desk", "location_name": desk.name},
        ["can_redeem", "valid_from", "valid_to"],
        as_dict=True,
    )
    if not location or not location.can_redeem:
        raise GiftCertificateError("Certificate is not accepted at this location", "CERT_LOCATION_NOT_ALLOWED")
    if location.valid_from and today < frappe.utils.getdate(location.valid_from):
        raise GiftCertificateError("Certificate location is not effective", "CERT_LOCATION_NOT_ALLOWED")
    if location.valid_to and today > frappe.utils.getdate(location.valid_to):
        raise GiftCertificateError("Certificate location has expired", "CERT_LOCATION_NOT_ALLOWED")
    entity = frappe.db.get_value(
        "UA Gift Certificate Network Entity",
        {"parent": certificate.network, "company": desk.company, "entity_role": ("in", ["Redeemer", "Both"])},
        ["fop_profile", "compliance_profile", "valid_from", "valid_to"],
        as_dict=True,
    )
    if not entity:
        raise GiftCertificateError("Certificate is not accepted by this entity", "CERT_ENTITY_NOT_ALLOWED")
    if entity.valid_from and today < frappe.utils.getdate(entity.valid_from):
        raise GiftCertificateError("Certificate entity is not effective", "CERT_ENTITY_NOT_ALLOWED")
    if entity.valid_to and today > frappe.utils.getdate(entity.valid_to):
        raise GiftCertificateError("Certificate entity has expired", "CERT_ENTITY_NOT_ALLOWED")
    if desk.company != certificate.issuer_company and not frappe.db.get_single_value(
        "UA Gift Certificate Settings", "cross_entity_enabled"
    ):
        raise GiftCertificateError("Cross-entity redemption is disabled", "CERT_ENTITY_NOT_ALLOWED")
    resolve_profile(
        company=desk.company,
        fop_profile=entity.fop_profile,
        profile_name=entity.compliance_profile,
        action="redeem",
    )


def _eligible_lines(program, order):
    rules = sorted(program.eligibility_rules, key=lambda row: (-int(row.priority or 0), row.idx))
    previously_reserved = _reserved_amount_by_row(order.name)
    lines = []
    ineligible = []
    for row in order.items:
        remaining = max(ZERO, money(row.amount) - previously_reserved.get(row.name, ZERO))
        allowed, reason = _rule_decision(rules, row)
        if allowed and remaining > ZERO:
            lines.append(EligibleLine(row.name, remaining))
        else:
            ineligible.append({"row_name": row.name, "code": reason or "ITEM_NOT_ELIGIBLE"})
    return lines, ineligible


def _reserved_amount_by_row(order_name: str) -> dict[str, object]:
    result = {}
    reservations = frappe.get_all(
        "UA Gift Certificate Reservation",
        filters={"pos_order": order_name, "status": ("in", ["Active", "Consuming"])},
        fields=["policy_snapshot_json"],
    )
    for reservation in reservations:
        payload = json.loads(reservation.policy_snapshot_json or "{}")
        for allocation in payload.get("allocations", []):
            row_name = allocation["row"]
            result[row_name] = money(result.get(row_name, ZERO) + money(allocation["amount"]))
    return result


def _rule_decision(rules, row):
    if not rules:
        return True, None
    item_group = frappe.db.get_value("Item", row.item_code, "item_group")
    brand = frappe.db.get_value("Item", row.item_code, "brand")
    matching = [
        rule
        for rule in rules
        if (rule.subject_type == "Item" and rule.subject == row.item_code)
        or (rule.subject_type == "Item Group" and rule.subject == item_group)
        or (rule.subject_type == "Brand" and rule.subject == brand)
    ]
    if not matching:
        return True, None
    winner = matching[0]
    return winner.effect == "Allow", winner.reason or "ITEM_NOT_ELIGIBLE"


def _audit_failed_token(digest: str, reason: str):
    frappe.logger("ua_gift_certificates").warning(
        {"event": "gift_certificate_token_failed", "token_hash_prefix": digest[:12], "reason": reason}
    )
