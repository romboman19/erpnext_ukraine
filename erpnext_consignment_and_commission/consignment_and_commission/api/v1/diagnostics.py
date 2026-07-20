import frappe

from ...integrations.reconciliation import audit_financial_integrity
from ...services.diagnostics import collect_environment
from .common import AUDIT_ROLES, assert_roles


@frappe.whitelist(methods=["GET"])
def readiness() -> dict:
    """Return read-only compatibility and foundation readiness evidence."""
    frappe.only_for("System Manager")
    return collect_environment()


@frappe.whitelist(methods=["GET"])
def financial_integrity(company: str | None = None) -> dict:
    """Reconcile domain snapshots, partner debt, payments and reservations."""
    assert_roles(AUDIT_ROLES)
    return audit_financial_integrity(company=company or None)
