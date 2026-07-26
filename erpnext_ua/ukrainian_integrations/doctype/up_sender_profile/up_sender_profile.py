import re

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_ua.integrations.utils.validation import validate_allowed_host, validate_http_url


class UPSenderProfile(Document):
    def validate(self):
        validate_http_url(self.api_base, "Ukrposhta API Base")
        validate_allowed_host(
            self.api_base,
            "Ukrposhta API Base",
            default_hosts={"www.ukrposhta.ua"},
            config_key="ukrposhta_allowed_api_hosts",
        )
        if int(self.is_active or 0) == 1 and not self.get_password("ecom_token", raise_exception=False):
            frappe.throw(_("Active Ukrposhta profile requires an eCom token"))
        phone = re.sub(r"\D", "", self.sender_phone or "")
        if int(self.is_active or 0) == 1 and (len(phone) != 12 or not phone.startswith("380")):
            frappe.throw(_("Active Ukrposhta profile requires a valid Ukrainian sender phone"))
        if int(self.is_active or 0) == 1 and not re.fullmatch(r"\d{5}", str(self.postcode or "")):
            frappe.throw(_("Active Ukrposhta profile requires a five-digit postcode"))
        if int(self.is_default or 0) == 1 and int(self.is_active or 0) != 1:
            frappe.throw(_("The default Ukrposhta profile must be active"))
        if int(self.is_default or 0) == 1:
            frappe.db.sql("SELECT name FROM `tabDocType` WHERE name = %s FOR UPDATE", ("UP Sender Profile",))
            existing = frappe.db.get_value("UP Sender Profile", {"is_default": 1, "name": ["!=", self.name]}, "name")
            if existing:
                frappe.throw(_("Only one default Ukrposhta profile is allowed"))
