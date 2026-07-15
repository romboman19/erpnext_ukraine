import frappe
from frappe import _
from frappe.model.document import Document

from ukrainian_integrations.utils.validation import validate_http_url, validate_profile_rows


class LiqPaySettings(Document):
    def validate(self):
        rows = self.get("profiles") or []
        validate_profile_rows(rows, "LiqPay")
        enabled_public_keys = [
            str(row.get("public_key") or "").strip()
            for row in rows
            if int(row.get("enabled") or 0) == 1 and str(row.get("public_key") or "").strip()
        ]
        if len(enabled_public_keys) != len(set(enabled_public_keys)):
            frappe.throw(_("Enabled LiqPay profiles must use unique public keys"))
        for row in rows:
            if row.get("result_url"):
                validate_http_url(row.get("result_url"), "LiqPay Result URL")
            if row.get("server_url"):
                validate_http_url(row.get("server_url"), "LiqPay Server URL")
            private_key = (
                row.get_password("private_key", raise_exception=False) if hasattr(row, "get_password") else ""
            )
            if int(row.get("enabled") or 0) == 1 and (
                not row.get("public_key") or not private_key or not row.get("server_url")
            ):
                frappe.throw(
                    _("Enabled LiqPay profile {0} requires public/private keys and Server URL").format(
                        row.get("label")
                    )
                )
            if int(row.get("auto_create_payment_entry") or 0) == 1:
                if not row.get("bank_account") or not row.get("mode_of_payment") or not row.get("company"):
                    frappe.throw(
                        _(
                            "LiqPay profile {0}: auto reconciliation requires Bank Account, Mode of Payment and Company"
                        ).format(row.get("label"))
                    )
                bank_company = frappe.db.get_value("Bank Account", row.get("bank_account"), "company")
                if bank_company and bank_company != row.get("company"):
                    frappe.throw(_("LiqPay Bank Account belongs to a different company"))
