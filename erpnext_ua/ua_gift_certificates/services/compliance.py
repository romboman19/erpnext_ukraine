from __future__ import annotations

import frappe

from erpnext_ua.ua_gift_certificates.domain.compliance import ensure_allowed
from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError


def resolve_profile(*, company: str, fop_profile: str | None, profile_name: str | None, action: str, corrective=False):
    today = frappe.utils.today()
    if profile_name:
        profile = frappe.get_doc("UA Gift Certificate Compliance Profile", profile_name)
    else:
        filters = {
            "company": company,
            "status": "Active",
            "valid_from": ("<=", today),
        }
        if fop_profile:
            filters["fop_profile"] = fop_profile
        else:
            filters["fop_profile"] = ("is", "not set")
        candidates = frappe.get_all(
            "UA Gift Certificate Compliance Profile",
            filters=filters,
            fields=["name", "valid_to"],
            order_by="version desc",
        )
        names = [
            row.name
            for row in candidates
            if not row.valid_to or frappe.utils.getdate(row.valid_to) >= frappe.utils.getdate(today)
        ]
        if len(names) != 1:
            raise GiftCertificateError("No unambiguous active compliance profile", "CERT_COMPLIANCE_DENIED")
        profile = frappe.get_doc("UA Gift Certificate Compliance Profile", names[0])
    if profile.company != company or (profile.fop_profile or None) != (fop_profile or None):
        raise GiftCertificateError("Compliance profile belongs to another entity", "CERT_ENTITY_NOT_ALLOWED")
    if profile.valid_from and frappe.utils.getdate(profile.valid_from) > frappe.utils.getdate(today):
        raise GiftCertificateError("Compliance profile is not effective", "CERT_COMPLIANCE_PROFILE_EXPIRED")
    if profile.valid_to and frappe.utils.getdate(profile.valid_to) < frappe.utils.getdate(today):
        raise GiftCertificateError("Compliance profile has expired", "CERT_COMPLIANCE_PROFILE_EXPIRED")
    return profile, ensure_allowed(profile, action, corrective=corrective)
