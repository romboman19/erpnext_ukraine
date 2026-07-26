"""Shared authentication and strict input parsing for write APIs."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import frappe

OPERATOR_ROLES = ("System Manager", "Commission Trade Manager", "Commission Trade User")
MANAGER_ROLES = ("System Manager", "Commission Trade Manager")
AUDIT_ROLES = ("System Manager", "Commission Trade Manager", "Commission Trade Auditor")


def assert_roles(allowed_roles: tuple[str, ...]) -> None:
    if frappe.session.user == "Guest" or not any(
        role in frappe.get_roles() for role in allowed_roles
    ):
        frappe.throw("A permitted Commission Trade role is required", frappe.PermissionError)


def assert_permission(
    doctype: str,
    permission_type: str,
    name: str | None = None,
) -> None:
    """Require the caller's native Frappe permission before a privileged service write."""
    if name and not frappe.db.exists(doctype, name):
        frappe.throw(f"{doctype} {name} does not exist", frappe.DoesNotExistError)
    document = frappe.get_doc(doctype, name) if name else None
    if not frappe.has_permission(doctype, permission_type, doc=document):
        target = f" {name}" if name else ""
        frappe.throw(
            f"Not permitted to {permission_type} {doctype}{target}",
            frappe.PermissionError,
        )


def parse_json(value: Any, *, label: str) -> Any:
    if isinstance(value, str):
        try:
            return frappe.parse_json(value)
        except Exception as exc:
            raise ValueError(f"{label} must be valid JSON") from exc
    return value


def parse_decimal(value: Any, *, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be a decimal number") from exc
    if not result.is_finite():
        raise ValueError(f"{label} must be finite")
    return result


def parse_bool(value: Any, *, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{label} must be a boolean")
