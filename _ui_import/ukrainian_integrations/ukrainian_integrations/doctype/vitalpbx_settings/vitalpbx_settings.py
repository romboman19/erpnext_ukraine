import frappe
from frappe import _
from frappe.model.document import Document

from ukrainian_integrations.utils.validation import validate_http_url


class VitalPBXSettings(Document):
    def validate(self):
        validate_http_url(self.base_url, "VitalPBX Base URL", allow_http=bool(frappe.conf.get("vitalpbx_allow_insecure_http")))
        if int(self.enabled or 0) == 1:
            if not self.get_password("api_key", raise_exception=False):
                frappe.throw(_("Enabled VitalPBX integration requires an API key"))
            if not self.get_password("webhook_key", raise_exception=False):
                frappe.throw(_("Enabled VitalPBX integration requires a webhook key"))
