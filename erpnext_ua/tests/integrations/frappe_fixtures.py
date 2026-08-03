from __future__ import annotations

import frappe

CUSTOMER_GROUP = "_UA Integration Customers"
TERRITORY = "_UA Integration Territory"


def ensure_customer_master_links() -> tuple[str, str]:
    """Create the leaf masters required by Customer on an empty ERPNext site."""
    customer_group = ensure_leaf_master(
        doctype="Customer Group",
        name=CUSTOMER_GROUP,
        name_field="customer_group_name",
        parent_field="parent_customer_group",
    )
    territory = ensure_leaf_master(
        doctype="Territory",
        name=TERRITORY,
        name_field="territory_name",
        parent_field="parent_territory",
    )
    return customer_group, territory


def ensure_company_fiscal_year(company: str, name: str) -> str:
    """Create a current-year fiscal period scoped to one test company."""
    if frappe.db.exists("Fiscal Year", name):
        return name
    year = frappe.utils.getdate().year
    return frappe.get_doc(
        {
            "doctype": "Fiscal Year",
            "year": name,
            "year_start_date": f"{year}-01-01",
            "year_end_date": f"{year}-12-31",
            "companies": [{"company": company}],
        }
    ).insert(ignore_permissions=True).name


def ensure_selling_price_list(name: str, currency: str) -> str:
    """Create a selling price list required by clean-site invoices."""
    if frappe.db.exists("Price List", name):
        return name
    return frappe.get_doc(
        {
            "doctype": "Price List",
            "price_list_name": name,
            "currency": currency,
            "selling": 1,
            "enabled": 1,
        }
    ).insert(ignore_permissions=True).name


def ensure_leaf_master(*, doctype: str, name: str, name_field: str, parent_field: str) -> str:
    """Create one leaf below the first available group in a tree master."""
    if frappe.db.exists(doctype, name):
        return name

    parent = frappe.db.get_value(
        doctype,
        {"is_group": 1},
        "name",
        order_by="lft asc",
    )
    if not parent:
        parent = frappe.get_doc(
            {
                "doctype": doctype,
                name_field: f"_UA Integration {doctype} Root",
                "is_group": 1,
            }
        ).insert(ignore_permissions=True).name

    return frappe.get_doc(
        {
            "doctype": doctype,
            name_field: name,
            parent_field: parent,
            "is_group": 0,
        }
    ).insert(ignore_permissions=True).name
