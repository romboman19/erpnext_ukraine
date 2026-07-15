import string

import frappe
from frappe import _
from frappe.model.document import Document


class CustomerIdentificationSettings(Document):
    def validate(self):
        for fieldname, minimum, maximum, label in (
            ("ttl_minutes", 1, 60, "TTL"),
            ("max_attempts", 1, 10, "Maximum attempts"),
            ("rate_limit_per_10_min", 1, 50, "Rate limit"),
        ):
            value = int(self.get(fieldname) or 0)
            if value < minimum or value > maximum:
                frappe.throw(
                    _("{0} must be between {1} and {2}").format(
                        label,
                        minimum,
                        maximum,
                    )
                )

        if self.sms_enabled:
            template = str(self.sms_template or "")
            fields = {
                field_name
                for _, field_name, _, _ in string.Formatter().parse(template)
                if field_name
            }
            if not template or not {"code", "minutes"}.issubset(fields):
                frappe.throw(
                    _("SMS template must contain {code} and {minutes}")
                )

        if self.telegram_enabled:
            if not str(self.telegram_bot_username or "").strip():
                frappe.throw(_("Telegram bot username is required"))
            if not self.get_password("telegram_bot_token", raise_exception=False):
                frappe.throw(_("Telegram bot token is required"))
            if not self.get_password(
                "telegram_webhook_secret",
                raise_exception=False,
            ):
                frappe.throw(_("Telegram webhook secret is required"))

        if self.call_enabled and not str(
            self.call_verification_number or ""
        ).strip():
            frappe.throw(_("Call verification number is required"))
