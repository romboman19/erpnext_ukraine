"""Test-site-only Stage 1 foundation metadata and validation smoke."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from .accounting import _ensure_accounts, _ensure_supplier
from .fifo import _ensure_location_warehouses

CONFIRMATION = "RUN_STAGE_1_FOUNDATION"
ALLOWED_SITES = frozenset({"postest.local", "postest-restore.local"})
REQUIRED_DOCTYPES = (
    "CC Settings",
    "CC Location",
    "CC Partner Profile",
    "CC Contract",
    "CC Account Mapping",
)
REQUIRED_ROLES = (
    "Commission Trade Manager",
    "Commission Trade User",
    "Commission Trade Auditor",
)


def _assert_test_scope(frappe: Any, *, confirm_site: str, confirm_write: str, company: str) -> None:
    if frappe.local.site not in ALLOWED_SITES or confirm_site != frappe.local.site:
        raise RuntimeError("Stage 1 foundation smoke is restricted to an allow-listed test site")
    if confirm_write != CONFIRMATION:
        raise RuntimeError(f"Explicit confirmation {CONFIRMATION!r} is required")
    if company != "POS Test Ukraine":
        raise RuntimeError("Stage 1 foundation smoke is restricted to the POS Test Ukraine fixture")


def run_foundation_flow(
    confirm_site: str,
    confirm_write: str,
    company: str = "POS Test Ukraine",
) -> dict[str, Any]:
    import frappe
    from frappe.utils import nowdate

    _assert_test_scope(
        frappe,
        confirm_site=confirm_site,
        confirm_write=confirm_write,
        company=company,
    )

    run_id = uuid4().hex[:10].upper()
    created: list[tuple[str, str]] = []
    cleanup_errors: list[str] = []
    result: dict[str, Any] = {"site": frappe.local.site, "company": company, "run_id": run_id}

    try:
        missing_doctypes = [doctype for doctype in REQUIRED_DOCTYPES if not frappe.db.exists("DocType", doctype)]
        missing_roles = [role for role in REQUIRED_ROLES if not frappe.db.exists("Role", role)]
        workspace_present = bool(frappe.db.exists("Workspace", "Commission Trade"))
        if missing_doctypes or missing_roles or not workspace_present:
            raise AssertionError(
                f"Foundation metadata incomplete: doctypes={missing_doctypes}, roles={missing_roles}, "
                f"workspace={workspace_present}"
            )

        _, warehouses = _ensure_location_warehouses(frappe, company)
        accounts = _ensure_accounts(frappe, company, require_payment_accounts=False)
        supplier_name = f"TP Stage 1 Supplier {run_id}"
        supplier = _ensure_supplier(
            frappe,
            company=company,
            supplier_name=supplier_name,
            payable_account=accounts["supplier_payable"],
        )
        created.append(("Supplier", supplier))

        location = frappe.get_doc(
            {
                "doctype": "CC Location",
                "location_name": f"TP Stage 1 Location {run_id}",
                "company": company,
                "legal_entity_type": "Company",
                "legal_entity_name": company,
                "own_warehouse": warehouses["OWN"],
                "commission_warehouse": warehouses["COMMISSION"],
                "consignment_warehouse": warehouses["CONSIGNMENT"],
            }
        ).insert(ignore_permissions=True)
        created.append(("CC Location", location.name))

        partner = frappe.get_doc(
            {
                "doctype": "CC Partner Profile",
                "partner_name": f"TP Stage 1 Partner {run_id}",
                "supplier": supplier,
                "allowed_relationship_models": "BOTH",
                "default_currency": frappe.get_cached_value("Company", company, "default_currency"),
                "default_settlement_deadline_days": 7,
            }
        ).insert(ignore_permissions=True)
        created.append(("CC Partner Profile", partner.name))

        mapping_created = False
        mapping = frappe.db.exists("CC Account Mapping", company)
        if not mapping:
            mapping_doc = frappe.get_doc(
                {
                    "doctype": "CC Account Mapping",
                    "company": company,
                    "off_balance_goods_account": accounts["off_balance_goods"],
                    "gross_proceeds_clearing_account": accounts["commission_gross_proceeds"],
                    "commission_revenue_account": accounts["commission_revenue"],
                    "principal_proceeds_deduction_account": accounts[
                        "principal_proceeds_deduction"
                    ],
                    "unreported_commission_liability_account": accounts["unreported_commission_liability"],
                    "unreported_consignment_liability_account": accounts["unreported_consignment_liability"],
                    "default_supplier_payable_account": accounts["supplier_payable"],
                }
            ).insert(ignore_permissions=True)
            mapping = mapping_doc.name
            mapping_created = True
            created.append(("CC Account Mapping", mapping))
        else:
            frappe.get_doc("CC Account Mapping", mapping).run_method("validate")

        contract = frappe.get_doc(
            {
                "doctype": "CC Contract",
                "contract_title": f"TP Stage 1 Commission {run_id}",
                "status": "ACTIVE",
                "partner_profile": partner.name,
                "company": company,
                "location": location.name,
                "relationship_model": "COMMISSION",
                "currency": frappe.get_cached_value("Company", company, "default_currency"),
                "commission_rate": 15,
                "valid_from": nowdate(),
                "settlement_frequency": "MONTHLY",
                "settlement_deadline_days": 7,
                "fiscal_policy": "AUTO",
                "price_authority": "COMPANY",
            }
        ).insert(ignore_permissions=True)
        created.append(("CC Contract", contract.name))

        overlap_rejected = False
        try:
            frappe.get_doc(
                {
                    "doctype": "CC Contract",
                    "contract_title": f"TP Stage 1 Overlap {run_id}",
                    "status": "ACTIVE",
                    "partner_profile": partner.name,
                    "company": company,
                    "location": location.name,
                    "relationship_model": "COMMISSION",
                    "currency": contract.currency,
                    "commission_rate": 10,
                    "valid_from": nowdate(),
                    "settlement_frequency": "MONTHLY",
                    "settlement_deadline_days": 7,
                    "fiscal_policy": "AUTO",
                    "price_authority": "COMPANY",
                }
            ).insert(ignore_permissions=True)
        except frappe.ValidationError as exc:
            overlap_rejected = "overlaps CC Contract" in str(exc)
        if not overlap_rejected:
            raise AssertionError("An overlapping active contract was not rejected")

        result.update(
            {
                "metadata": {
                    "doctypes": list(REQUIRED_DOCTYPES),
                    "roles": list(REQUIRED_ROLES),
                    "workspace": "Commission Trade",
                },
                "documents": {
                    "location": location.name,
                    "partner": partner.name,
                    "contract": contract.name,
                    "account_mapping": mapping,
                },
                "legal_entity": {
                    "type": location.legal_entity_type,
                    "name": location.legal_entity_name,
                    "label": location.legal_entity_label,
                },
                "overlap_rejected": overlap_rejected,
                "mapping_created": mapping_created,
            }
        )
    finally:
        for doctype, name in reversed(created):
            try:
                if frappe.db.exists(doctype, name):
                    frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
            except Exception as exc:
                cleanup_errors.append(f"{doctype} {name}: {exc}")
        frappe.db.commit()

    result["cleanup_errors"] = cleanup_errors
    result["remaining_documents"] = {
        f"{doctype}:{name}": bool(frappe.db.exists(doctype, name)) for doctype, name in created
    }
    if cleanup_errors or any(result["remaining_documents"].values()):
        raise AssertionError(f"Stage 1 foundation cleanup failed: {result}")
    return result
