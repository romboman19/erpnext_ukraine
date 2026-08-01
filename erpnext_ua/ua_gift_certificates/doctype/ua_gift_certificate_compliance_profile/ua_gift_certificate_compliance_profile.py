import frappe
from frappe.model.document import Document


class UAGiftCertificateComplianceProfile(Document):
    def validate(self):
        if self.status in {"Approved", "Active"}:
            if not self.approved_by or not self.approved_at:
                frappe.throw("Approved compliance profile requires approver and timestamp")
            if (
                any(
                    int(self.get(field) or 0)
                    for field in (
                        "allow_sale",
                        "allow_redemption_as_payment",
                        "allow_cross_entity",
                        "allow_forfeit_remainder",
                        "allow_breakage_recognition",
                    )
                )
                and not self.legal_basis_file
            ):
                frappe.throw("A legal basis attachment is required for enabling certificate operations")
        if self.vat_status == "VAT Payer" and self.vat_mode != "Blocked":
            if not frappe.db.get_single_value("UA Gift Certificate Settings", "vat_profiles_enabled"):
                frappe.throw("VAT certificate profiles are not enabled")

    def before_save(self):
        if not self.prepared_by:
            self.prepared_by = frappe.session.user
