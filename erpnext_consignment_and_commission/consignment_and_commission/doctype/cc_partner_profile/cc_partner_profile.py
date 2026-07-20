import frappe
from frappe.model.document import Document

from ...services.foundation import FoundationValidationError, validate_partner_relationship_model


class CCPartnerProfile(Document):
    def validate(self) -> None:
        try:
            validate_partner_relationship_model(self.allowed_relationship_models)
        except FoundationValidationError as exc:
            frappe.throw(str(exc))

        supplier = frappe.db.get_value("Supplier", self.supplier, ["supplier_name", "disabled"], as_dict=True)
        if not supplier:
            frappe.throw(f"Supplier {self.supplier} does not exist")
        if supplier.disabled:
            frappe.throw(f"Supplier {self.supplier} is disabled")
        if not self.partner_name:
            self.partner_name = supplier.supplier_name or self.supplier
