import frappe
from frappe.model.document import Document

ACCOUNT_ROOTS = {
    "paid_liability_account": "Liability",
    "redemption_clearing_account": "Asset",
    "promotional_expense_account": "Expense",
    "premium_revenue_account": "Income",
    "breakage_income_account": "Income",
    "refund_clearing_account": "Asset",
    "settlement_receivable_account": "Asset",
    "settlement_payable_account": "Liability",
}


class UAGiftCertificateAccountingProfile(Document):
    def validate(self):
        for fieldname, root_type in ACCOUNT_ROOTS.items():
            account = self.get(fieldname)
            if not account:
                continue
            values = frappe.db.get_value(
                "Account", account, ["company", "root_type", "is_group", "disabled"], as_dict=True
            )
            if not values or values.company != self.company or values.root_type != root_type:
                frappe.throw(f"{fieldname} must be a {root_type} account of {self.company}")
            if values.is_group or values.disabled:
                frappe.throw(f"{fieldname} must be an enabled ledger account")
        cost_center = frappe.db.get_value(
            "Cost Center",
            self.default_cost_center,
            ["company", "is_group", "disabled"],
            as_dict=True,
        )
        if not cost_center or cost_center.company != self.company:
            frappe.throw(f"default_cost_center must belong to {self.company}")
        if cost_center.is_group or cost_center.disabled:
            frappe.throw("default_cost_center must be an enabled ledger Cost Center")
