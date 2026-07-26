"""Explicit Ukrainian PSBO account mapping for commission trade."""

from __future__ import annotations

from typing import Any

import frappe

FULL_291 = {
    "off_balance_goods_account": "024",
    "gross_proceeds_clearing_account": "702",
    "commission_revenue_account": "703",
    "principal_proceeds_deduction_account": "704",
    "unreported_commission_liability_account": "685",
    "unreported_consignment_liability_account": "685",
}

SIMPLIFIED_EXISTING = {
    "off_balance_goods_account": "024",
    "gross_proceeds_clearing_account": "70.1",
    "principal_proceeds_deduction_account": "70.2",
}

SIMPLIFIED_EXTENSIONS = {
    "commission_revenue_account": (
        "70.3",
        "Дохід від комісійних та консигнаційних послуг",
        "70",
    ),
    "unreported_commission_liability_account": (
        "68.6",
        "Розрахунки з комітентами, не підтверджені звітом",
        "68",
    ),
    "unreported_consignment_liability_account": (
        "68.7",
        "Розрахунки з консигнантами, не підтверджені звітом",
        "68",
    ),
}


def _account_by_number(frappe: Any, company: str, account_number: str) -> str:
    account = frappe.db.get_value(
        "Account",
        {"company": company, "account_number": account_number, "is_group": 0},
        "name",
    )
    if not account:
        frappe.throw(f"Company {company} has no ledger account {account_number}")
    return account


def _ensure_simplified_extension(
    frappe: Any,
    *,
    company: str,
    account_number: str,
    account_name: str,
    parent_number: str,
) -> str:
    existing = frappe.db.get_value(
        "Account",
        {"company": company, "account_number": account_number, "is_group": 0},
        "name",
    )
    if existing:
        return existing
    parent = frappe.db.get_value(
        "Account",
        {"company": company, "account_number": parent_number, "is_group": 1},
        "name",
    )
    if not parent:
        frappe.throw(
            f"Simplified chart requires group account {parent_number} before creating {account_number}"
        )
    currency = frappe.get_cached_value("Company", company, "default_currency")
    return frappe.get_doc(
        {
            "doctype": "Account",
            "company": company,
            "account_name": account_name,
            "account_number": account_number,
            "parent_account": parent,
            "is_group": 0,
            "account_currency": currency,
            "ua_chart_template": "simplified_186",
            "ua_legal_source": "erpnext_extension",
        }
    ).insert(ignore_permissions=True).name


def _resolved_values(frappe: Any, company: str, template: str) -> dict[str, str]:
    if template == "full_291":
        return {
            fieldname: _account_by_number(frappe, company, account_number)
            for fieldname, account_number in FULL_291.items()
        }
    if template != "simplified_186":
        frappe.throw(
            "Commission trade requires the full #291 or simplified #186 chart from erpnext_ua"
        )
    values = {
        fieldname: _account_by_number(frappe, company, account_number)
        for fieldname, account_number in SIMPLIFIED_EXISTING.items()
    }
    for fieldname, (number, name, parent) in SIMPLIFIED_EXTENSIONS.items():
        values[fieldname] = _ensure_simplified_extension(
            frappe,
            company=company,
            account_number=number,
            account_name=name,
            parent_number=parent,
        )
    return values


def configure_psbo_account_mapping(company: str, *, overwrite: bool = False) -> Any:
    """Create or align one Company mapping after an explicit administrator action."""
    frappe.only_for(["Accounts Manager", "System Manager"])
    frappe.has_permission("Company", "write", company, throw=True)
    company_values = frappe.db.get_value(
        "Company",
        company,
        ["country", "ua_chart_template", "default_payable_account"],
        as_dict=True,
    )
    if not company_values or company_values.country != "Ukraine":
        frappe.throw("PSBO commission mapping requires a Ukrainian Company")
    values = _resolved_values(frappe, company, company_values.ua_chart_template)
    if not company_values.default_payable_account:
        frappe.throw("Company requires a default Supplier Payable account")
    values["default_supplier_payable_account"] = company_values.default_payable_account

    existing = frappe.db.exists("CC Account Mapping", company)
    mapping = (
        frappe.get_doc("CC Account Mapping", company)
        if existing
        else frappe.get_doc({"doctype": "CC Account Mapping", "company": company})
    )
    for fieldname, value in values.items():
        if overwrite or not mapping.get(fieldname):
            mapping.set(fieldname, value)
    if existing:
        mapping.save(ignore_permissions=True)
    else:
        mapping.insert(ignore_permissions=True)
    return mapping


@frappe.whitelist()
def setup_psbo_account_mapping(company: str, overwrite: int = 0) -> dict[str, str]:
    mapping = configure_psbo_account_mapping(company, overwrite=bool(int(overwrite)))
    return {
        "name": mapping.name,
        "company": mapping.company,
        "off_balance_goods_account": mapping.off_balance_goods_account,
        "gross_proceeds_clearing_account": mapping.gross_proceeds_clearing_account,
        "commission_revenue_account": mapping.commission_revenue_account,
        "principal_proceeds_deduction_account": mapping.principal_proceeds_deduction_account,
        "unreported_commission_liability_account": (
            mapping.unreported_commission_liability_account
        ),
        "unreported_consignment_liability_account": (
            mapping.unreported_consignment_liability_account
        ),
        "default_supplier_payable_account": mapping.default_supplier_payable_account,
    }
