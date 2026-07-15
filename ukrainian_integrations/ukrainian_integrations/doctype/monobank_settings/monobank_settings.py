import frappe
from frappe import _
from frappe.model.document import Document

from ukrainian_integrations.utils.validation import validate_profile_rows


class MonobankSettings(Document):
    def validate(self):
        rows = self.get("profiles") or []
        validate_profile_rows(rows, "Monobank")
        for row in rows:
            token = row.get_password("token", raise_exception=False) if hasattr(row, "get_password") else ""
            if int(row.get("enabled") or 0) == 1 and not token:
                frappe.throw(_("Enabled Monobank profile {0} requires a token").format(row.get("label")))
            if int(row.get("auto_import_enabled") or 0) == 1:
                if not row.get("account") or not row.get("bank_account") or not row.get("company"):
                    frappe.throw(
                        _("Monobank profile {0}: auto import requires Account, Bank Account and Company").format(
                            row.get("label")
                        )
                    )
                if not 1 <= int(row.get("auto_import_days_back") or 0) <= 31:
                    frappe.throw(_("Monobank auto-import days must be between 1 and 31"))
                bank_company = frappe.db.get_value("Bank Account", row.get("bank_account"), "company")
                if bank_company and bank_company != row.get("company"):
                    frappe.throw(_("Monobank Bank Account belongs to a different company"))
