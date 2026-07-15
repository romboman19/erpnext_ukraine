import re

import frappe
from frappe import _
from frappe.model.document import Document

from ukrainian_integrations.utils.validation import validate_allowed_host, validate_http_url

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
)


class RZDeliverySenderProfile(Document):
    def validate(self):
        validate_http_url(self.api_base, "Rozetka Delivery API Base")
        validate_allowed_host(
            self.api_base,
            "Rozetka Delivery API Base",
            default_hosts={"rz-delivery.rozetka.ua"},
            config_key="rozetka_delivery_allowed_api_hosts",
        )
        if self.content_language not in {"uk", "en", "ru"}:
            frappe.throw(_("Rozetka Delivery content language must be uk, en or ru"))
        if int(self.is_active or 0) == 1 and not self.get_password(
            "api_token", raise_exception=False
        ):
            frappe.throw(_("Active Rozetka Delivery profile requires a static API token"))

        phone = re.sub(r"\D", "", self.sender_phone or "")
        if int(self.is_active or 0) == 1 and (len(phone) != 12 or not phone.startswith("380")):
            frappe.throw(_("Active Rozetka Delivery profile requires a valid Ukrainian sender phone"))
        if int(self.is_active or 0) == 1:
            for fieldname, label in (
                ("sender_city_id", "Sender City ID"),
                ("sender_department_id", "Sender Department ID"),
            ):
                if not _UUID_RE.fullmatch(str(self.get(fieldname) or "")):
                    frappe.throw(_("{0} must be a valid UUID").format(label))
        if self.carrier_id and not _UUID_RE.fullmatch(str(self.carrier_id)):
            frappe.throw(_("Carrier ID must be a valid UUID"))
        if int(self.is_active or 0) == 1 and not (
            str(self.sender_first_name or "").strip() and str(self.sender_last_name or "").strip()
        ):
            frappe.throw(_("Active Rozetka Delivery profile requires sender first and last name"))
        if int(self.is_default or 0) == 1 and int(self.is_active or 0) != 1:
            frappe.throw(_("The default Rozetka Delivery profile must be active"))
        if int(self.is_default or 0) == 1:
            frappe.db.sql(
                "SELECT name FROM `tabDocType` WHERE name = %s FOR UPDATE",
                ("RZ Delivery Sender Profile",),
            )
            existing = frappe.db.get_value(
                "RZ Delivery Sender Profile",
                {"is_default": 1, "name": ["!=", self.name]},
                "name",
            )
            if existing:
                frappe.throw(_("Only one default Rozetka Delivery profile is allowed"))
