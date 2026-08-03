from __future__ import annotations

from collections.abc import Iterable

import frappe
from frappe import _

PRRO_ADMIN_ROLES = ("System Manager", "Accounts Manager")
PRRO_FISCALIZATION_ROLES = (*PRRO_ADMIN_ROLES, "Accounts User")


def require_roles(allowed_roles: Iterable[str]) -> None:
    allowed = set(allowed_roles)
    if allowed.intersection(frappe.get_roles(frappe.session.user)):
        return
    frappe.throw(
        _("Недостатньо прав для виконання операції ПРРО"),
        frappe.PermissionError,
    )


def require_document_permission(doc, permission_type: str) -> None:
    if frappe.has_permission(doc.doctype, permission_type, doc=doc):
        return
    frappe.throw(
        _("Недостатньо прав на документ {0} {1}").format(doc.doctype, doc.name),
        frappe.PermissionError,
    )


def require_register_control(register) -> None:
    require_roles(PRRO_ADMIN_ROLES)
    require_document_permission(register, "write")


def require_receipt_reconciliation(receipt, register) -> None:
    require_register_control(register)
    require_document_permission(receipt, "read")


def require_sales_invoice_fiscalization(invoice) -> None:
    require_roles(PRRO_FISCALIZATION_ROLES)
    require_document_permission(invoice, "read")
    if int(invoice.docstatus or 0) != 1:
        frappe.throw(_("Фіскалізувати можна лише проведений Sales Invoice"))
    if not invoice.get("is_pos") and not invoice.get("ua_ecommerce_channel"):
        frappe.throw(_("Ручна фіскалізація доступна лише для POS або ecommerce рахунку"))


def default_register_key(register) -> str:
    if register.default_kep_key:
        return register.default_kep_key
    frappe.throw(_("Для каси ПРРО не налаштовано КЕП"))
