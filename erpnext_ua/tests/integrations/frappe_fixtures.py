from __future__ import annotations

import frappe

CUSTOMER_GROUP = "_UA Integration Customers"
TERRITORY = "_UA Integration Territory"


def ensure_customer_master_links() -> tuple[str, str]:
    """Create the leaf masters required by Customer on an empty ERPNext site."""
    customer_group = _ensure_leaf(
        doctype="Customer Group",
        name=CUSTOMER_GROUP,
        name_field="customer_group_name",
        parent_field="parent_customer_group",
    )
    territory = _ensure_leaf(
        doctype="Territory",
        name=TERRITORY,
        name_field="territory_name",
        parent_field="parent_territory",
    )
    return customer_group, territory


def _ensure_leaf(*, doctype: str, name: str, name_field: str, parent_field: str) -> str:
    if frappe.db.exists(doctype, name):
        return name

    parent = frappe.db.get_value(
        doctype,
        {"is_group": 1},
        "name",
        order_by="lft asc",
    )
    if not parent:
        raise AssertionError(f"{doctype} root is required on the ERPNext test site")

    return frappe.get_doc(
        {
            "doctype": doctype,
            name_field: name,
            parent_field: parent,
            "is_group": 0,
        }
    ).insert(ignore_permissions=True).name
