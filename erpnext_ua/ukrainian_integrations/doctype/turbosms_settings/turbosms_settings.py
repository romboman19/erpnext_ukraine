import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_ua.integrations.utils.validation import validate_allowed_host, validate_http_url


class TurboSMSSettings(Document):
    def validate(self):
        validate_http_url(self.base_url, "TurboSMS API URL")
        validate_allowed_host(
            self.base_url,
            "TurboSMS API URL",
            default_hosts={"api.turbosms.ua"},
            config_key="turbosms_allowed_api_hosts",
        )
        if int(self.enabled or 0) == 1 and not self.get_password("token", raise_exception=False):
            frappe.throw(_("Enabled TurboSMS integration requires a token"))
        rows = self.get("senders") or []
        sender_names = [str(row.get("sender_name") or "").strip().casefold() for row in rows]
        sender_names = [name for name in sender_names if name]
        if len(sender_names) != len(set(sender_names)):
            frappe.throw(_("TurboSMS sender names must be unique"))
        if sum(1 for row in rows if int(row.get("is_default") or 0) == 1 and int(row.get("is_active") or 0) == 1) > 1:
            frappe.throw(_("Only one active default TurboSMS sender is allowed"))
