from __future__ import annotations

import frappe

from erpnext_ua.ua_gift_certificates.context import service_write
from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money

from .ledger import append_entry


def expire_due_certificates():
    if not frappe.db.table_exists("UA Gift Certificate"):
        return
    names = frappe.get_all(
        "UA Gift Certificate",
        filters={"status": ("in", ["Active", "Partially Redeemed"]), "valid_until": ("<", frappe.utils.today())},
        pluck="name",
        limit=100,
    )
    for name in names:
        try:
            frappe.db.sql("select name from `tabUA Gift Certificate` where name=%s for update", name)
            certificate = frappe.get_doc("UA Gift Certificate", name)
            if money(certificate.reserved_balance) > ZERO:
                continue
            program = frappe.get_doc("UA Gift Certificate Program", certificate.program)
            if program.expiry_accounting_policy == "Recognize Breakage":
                profile = frappe.get_doc("UA Gift Certificate Compliance Profile", program.compliance_profile)
                if not profile.allow_breakage_recognition:
                    certificate.db_set("status", "Manual Review", update_modified=False)
                    continue
                if money(certificate.paid_balance):
                    append_entry(
                        certificate,
                        transaction_type="Expire Paid",
                        paid_delta=-money(certificate.paid_balance),
                        idempotency_key=f"expiry:{name}:{certificate.valid_until}:paid",
                        reference_doctype="UA Gift Certificate",
                        reference_name=name,
                        reason_code="VALIDITY_EXPIRED",
                        values={"liability_delta": -money(certificate.paid_balance)},
                    )
                certificate.reload()
                if money(certificate.promotional_balance):
                    append_entry(
                        certificate,
                        transaction_type="Expire Promotional",
                        promotional_delta=-money(certificate.promotional_balance),
                        idempotency_key=f"expiry:{name}:{certificate.valid_until}:promotional",
                        reference_doctype="UA Gift Certificate",
                        reference_name=name,
                        reason_code="VALIDITY_EXPIRED",
                    )
            with service_write():
                certificate.reload()
                certificate.status = "Expired"
                certificate.expired_at = frappe.utils.now_datetime()
                certificate.expired_total = money(certificate.current_balance)
                certificate.available_balance = ZERO
                certificate.save(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(frappe.get_traceback(), f"Gift certificate expiry {name}")
