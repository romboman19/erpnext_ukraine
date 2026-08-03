"""Idempotent role provisioning for the GSF domain (§32)."""

from __future__ import annotations

from erpnext_ua.group_stock_fifo.services.readiness import REQUIRED_ROLES


def ensure_roles() -> list[str]:
    import frappe

    created = []
    for role in REQUIRED_ROLES:
        if frappe.db.exists("Role", role):
            continue
        frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
            ignore_permissions=True
        )
        created.append(role)
    return created
