import frappe
from frappe import _
from frappe.model.document import Document

from ukrainian_integrations.utils.validation import validate_http_url


class PBPOSSettings(Document):
    def validate(self):
        validate_http_url(
            self.gateway_url,
            "PB POS Gateway URL",
            allow_http=bool(frappe.conf.get("pb_pos_allow_insecure_http")),
        )
        timeout = int(self.request_timeout_sec or 0)
        if timeout < 5 or timeout > 180:
            frappe.throw(_("PB POS timeout must be between 5 and 180 seconds"))
        if int(self.enabled or 0) == 1 and not self.get_password("api_key", raise_exception=False):
            frappe.throw(_("Enabled PB POS integration requires an API key"))
