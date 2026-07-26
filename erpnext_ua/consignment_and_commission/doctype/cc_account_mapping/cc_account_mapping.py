import frappe
from frappe.model.document import Document

ACCOUNT_ROOT_TYPES = {
    "gross_proceeds_clearing_account": {"Income"},
    "commission_revenue_account": {"Income"},
    "principal_proceeds_deduction_account": {"Income"},
    "unreported_commission_liability_account": {"Liability"},
    "unreported_consignment_liability_account": {"Liability"},
    "default_supplier_payable_account": {"Liability"},
}

PSBO_ACCOUNT_PREFIXES = {
    "gross_proceeds_clearing_account": ("702", "70.1"),
    "commission_revenue_account": ("703", "70.3"),
    "principal_proceeds_deduction_account": ("704", "70.2"),
    "unreported_commission_liability_account": ("685", "68.6"),
    "unreported_consignment_liability_account": ("685", "68.7"),
}


class CCAccountMapping(Document):
    def validate(self) -> None:
        income_fields = (
            "gross_proceeds_clearing_account",
            "commission_revenue_account",
            "principal_proceeds_deduction_account",
        )
        income_accounts = [self.get(fieldname) for fieldname in income_fields]
        if len(set(income_accounts)) != len(income_accounts):
            frappe.throw("CC accounts 702, 703 and 704 must be distinct ledger accounts")
        for fieldname, root_types in ACCOUNT_ROOT_TYPES.items():
            account_name = self.get(fieldname)
            if not account_name:
                frappe.throw(f"CC Account Mapping requires {fieldname}")
            account = frappe.db.get_value(
                "Account",
                account_name,
                ["company", "account_number", "is_group", "root_type", "account_type"],
                as_dict=True,
            )
            if not account:
                frappe.throw(f"Account {account_name} does not exist")
            if account.company != self.company:
                frappe.throw(f"Account {account_name} must belong to company {self.company}")
            if account.is_group:
                frappe.throw(f"Account {account_name} must be a ledger account")
            if account.root_type not in root_types:
                allowed = ", ".join(sorted(root_types))
                frappe.throw(f"Account {account_name} must have root type {allowed}")
            prefixes = PSBO_ACCOUNT_PREFIXES.get(fieldname)
            if prefixes and not str(account.account_number or "").startswith(prefixes):
                allowed = " or ".join(prefixes)
                frappe.throw(
                    f"Account {account_name} must belong to Ukrainian account {allowed}"
                )
            if fieldname in {
                "unreported_commission_liability_account",
                "unreported_consignment_liability_account",
            } and account.account_type in {"Payable", "Receivable"}:
                frappe.throw(
                    f"Account {account_name} must be a non-party liability ledger"
                )
            if (
                fieldname == "default_supplier_payable_account"
                and account.account_type != "Payable"
            ):
                frappe.throw(f"Account {account_name} must have account type Payable")

        off_balance = frappe.db.get_value(
            "Account",
            self.off_balance_goods_account,
            ["company", "account_number", "is_group", "disabled", "ua_off_balance"],
            as_dict=True,
        )
        if (
            not off_balance
            or off_balance.company != self.company
            or off_balance.is_group
            or not off_balance.disabled
            or not off_balance.ua_off_balance
            or not str(off_balance.account_number or "").startswith("024")
        ):
            frappe.throw(
                "Off-balance Goods Account must be the disabled UA class-0 account 024 "
                "for this Company"
            )
