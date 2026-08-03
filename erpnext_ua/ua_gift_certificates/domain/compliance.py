from __future__ import annotations

from dataclasses import dataclass

from .errors import GiftCertificateError


@dataclass(frozen=True)
class ComplianceDecision:
    profile: str
    version: int
    action: str
    corrective: bool


ACTION_FIELDS = {
    "issue": "allow_issue",
    "sale": "allow_sale",
    "redeem": "allow_redemption_as_payment",
    "redeem_discount": "allow_redemption_as_discount",
    "cross_entity": "allow_cross_entity",
    "forfeit": "allow_forfeit_remainder",
    "breakage": "allow_breakage_recognition",
    "refund": "allow_refund",
}


def ensure_allowed(profile, action: str, *, corrective: bool = False) -> ComplianceDecision:
    fieldname = ACTION_FIELDS.get(action)
    if not fieldname:
        raise ValueError(f"unknown compliance action: {action}")
    if profile.status == "Revoked" and corrective and action in {"refund", "redeem"}:
        return ComplianceDecision(profile.name, int(profile.version), action, True)
    if profile.status != "Active" or not int(profile.get(fieldname) or 0):
        raise GiftCertificateError("Operation denied by compliance profile", "CERT_COMPLIANCE_DENIED")
    if profile.vat_status == "VAT Payer" and profile.vat_mode != "Supported":
        raise GiftCertificateError("VAT certificate profile is not supported", "CERT_VAT_PROFILE_NOT_SUPPORTED")
    return ComplianceDecision(profile.name, int(profile.version), action, corrective)
