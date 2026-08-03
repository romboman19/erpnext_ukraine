import frappe
from frappe.model.document import Document


class UAGiftCertificateSettings(Document):
    def validate(self):
        ttl = int(self.reservation_ttl_seconds or 600)
        if not 60 <= ttl <= 3600:
            frappe.throw("Reservation TTL must be between 60 and 3600 seconds")
        if self.enabled:
            from erpnext_ua.ua_gift_certificates.readiness import readiness_report

            report = readiness_report(include_settings_state=False)
            if report["status"] == "Blocked":
                frappe.throw("Gift Certificates are not ready: " + ", ".join(report["blocking_codes"]))
