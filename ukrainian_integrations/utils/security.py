from __future__ import annotations

import hmac
from collections.abc import Callable
from functools import wraps
from typing import TypeVar

import frappe
from frappe import _

F = TypeVar("F", bound=Callable)

SYSTEM_ROLES = ("System Manager",)
ACCOUNTS_ROLES = ("Accounts User", "Accounts Manager", "System Manager")
ACCOUNTS_MANAGER_ROLES = ("Accounts Manager", "System Manager")
SALES_ROLES = ("Sales User", "Sales Manager", "System Manager")
SALES_MANAGER_ROLES = ("Sales Manager", "System Manager")


def require_roles(*allowed_roles: str) -> None:
    """Fail closed unless the current user has one of the explicitly allowed roles."""
    user = getattr(getattr(frappe, "session", None), "user", None)
    if user == "Administrator":
        return

    assigned = set(frappe.get_roles(user))
    if not assigned.intersection(allowed_roles):
        frappe.throw(
            _("Недостатньо прав. Потрібна одна з ролей: {0}").format(", ".join(allowed_roles)),
            frappe.PermissionError,
        )


def roles_required(*allowed_roles: str):
    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapped(*args, **kwargs):
            require_roles(*allowed_roles)
            return fn(*args, **kwargs)

        return wrapped  # type: ignore[return-value]

    return decorator


def permitted_doc(doctype: str, name: str, permtype: str = "read"):
    doc = frappe.get_doc(doctype, name)
    doc.check_permission(permtype)
    return doc


def secrets_equal(supplied: str | None, expected: str | None) -> bool:
    return hmac.compare_digest((supplied or "").encode("utf-8"), (expected or "").encode("utf-8"))
