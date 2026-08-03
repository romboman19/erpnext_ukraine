from __future__ import annotations

import frappe

from erpnext_ua.ua_gift_certificates.services.batch import queue_batch
from erpnext_ua.ua_gift_certificates.services.issuance import issue_certificate
from erpnext_ua.ua_gift_certificates.services.reconciliation import reconcile_certificate


@frappe.whitelist(methods=["POST"])
def issue(**kwargs):
    frappe.only_for("Gift Certificate Manager")
    certificate, token = issue_certificate(**kwargs)
    frappe.db.commit()
    return {"certificate": certificate.name, "public_serial": certificate.public_serial, "one_time_token": token}


@frappe.whitelist(methods=["POST"])
def reconcile(certificate: str, repair_cache: int = 0):
    frappe.only_for(("Gift Certificate Accountant", "Gift Certificate Auditor"))
    result = reconcile_certificate(certificate, repair_cache=bool(int(repair_cache)))
    if repair_cache:
        frappe.db.commit()
    return result


@frappe.whitelist(methods=["POST"])
def generate_batch(batch: str):
    frappe.only_for("Gift Certificate Manager")
    return queue_batch(batch)
