import re

import frappe
from frappe import _
from frappe.model.document import Document


class NPSenderProfile(Document):
    def validate(self):
        if int(self.is_active or 0) == 1 and not self.get_password("api_key", raise_exception=False):
            frappe.throw(_("Active Nova Poshta profile requires an API key"))
        phone = re.sub(r"\D", "", self.phone or "")
        if int(self.is_active or 0) == 1 and (len(phone) != 12 or not phone.startswith("380")):
            frappe.throw(_("Active Nova Poshta profile requires a valid Ukrainian sender phone"))
        if int(self.is_default or 0) == 1 and int(self.is_active or 0) != 1:
            frappe.throw(_("The default Nova Poshta profile must be active"))
        if int(self.is_default or 0) == 1:
            frappe.db.sql("SELECT name FROM `tabDocType` WHERE name = %s FOR UPDATE", ("NP Sender Profile",))
            existing = frappe.db.get_value(
                "NP Sender Profile",
                {"company": self.company, "is_default": 1, "name": ["!=", self.name]},
                "name",
            )
            if existing:
                frappe.throw(_("Only one default Nova Poshta profile is allowed per company"))
        branches = self.get("sender_branches") or []
        if sum(1 for row in branches if int(row.get("is_default") or 0) == 1) > 1:
            frappe.throw(_("Only one default sender branch is allowed"))
        if int(self.is_active or 0) == 1:
            has_default_address = bool(self.default_settlement_ref and self.default_warehouse_ref)
            has_complete_branch = any(row.get("settlement_ref") and row.get("warehouse_ref") for row in branches)
            if not has_default_address and not has_complete_branch:
                frappe.throw(_("Active Nova Poshta profile requires a complete default address or sender branch"))
