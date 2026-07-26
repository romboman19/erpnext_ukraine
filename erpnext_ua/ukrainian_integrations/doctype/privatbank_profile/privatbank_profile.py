import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_ua.integrations.utils.validation import validate_allowed_host, validate_http_url


class PrivatBankProfile(Document):
    def validate(self):
        validate_http_url(self.api_base, "PrivatBank API Base")
        validate_allowed_host(
            self.api_base,
            "PrivatBank API Base",
            default_hosts={"acp.privatbank.ua"},
            config_key="privatbank_allowed_api_hosts",
        )
        if int(self.enabled or 0) == 1 and not self.get_password("token", raise_exception=False):
            frappe.throw(_("Enabled PrivatBank profile requires a token"))
        if int(self.auto_import_enabled or 0) == 1 and (not self.account or not self.bank_account or not self.company):
            frappe.throw(_("PrivatBank auto import requires Account, ERP Bank Account and Company"))
        if int(self.auto_import_days_back or 0) < 1 or int(self.auto_import_days_back or 0) > 366:
            frappe.throw(_("PrivatBank auto import days must be between 1 and 366"))
        if self.bank_account and self.company:
            bank_company = frappe.db.get_value("Bank Account", self.bank_account, "company")
            if bank_company and bank_company != self.company:
                frappe.throw(_("PrivatBank ERP Bank Account belongs to a different company"))
