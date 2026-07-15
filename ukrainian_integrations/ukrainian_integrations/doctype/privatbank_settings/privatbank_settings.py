import frappe
from frappe import _
from frappe.model.document import Document

from ukrainian_integrations.utils.validation import validate_allowed_host, validate_http_url, validate_profile_rows


class PrivatBankSettings(Document):
    def validate(self):
        rows = self.get("profiles") or []
        validate_profile_rows(rows, "PrivatBank")
        for row in rows:
            validate_http_url(row.get("api_base"), "PrivatBank API Base")
            validate_allowed_host(
                row.get("api_base"),
                "PrivatBank API Base",
                default_hosts={"acp.privatbank.ua"},
                config_key="privatbank_allowed_api_hosts",
            )
            token = row.get_password("token", raise_exception=False) if hasattr(row, "get_password") else ""
            if int(row.get("enabled") or 0) == 1 and not token:
                frappe.throw(_("Enabled PrivatBank profile {0} requires a token").format(row.get("label")))
            if int(row.get("auto_import_enabled") or 0) == 1:
                if not row.get("account") or not row.get("bank_account") or not row.get("company"):
                    frappe.throw(
                        _("PrivatBank profile {0}: auto import requires Account, Bank Account and Company").format(
                            row.get("label")
                        )
                    )
                if not 1 <= int(row.get("auto_import_days_back") or 0) <= 366:
                    frappe.throw(_("PrivatBank auto-import days must be between 1 and 366"))
                bank_company = frappe.db.get_value("Bank Account", row.get("bank_account"), "company")
                if bank_company and bank_company != row.get("company"):
                    frappe.throw(_("PrivatBank Bank Account belongs to a different company"))
