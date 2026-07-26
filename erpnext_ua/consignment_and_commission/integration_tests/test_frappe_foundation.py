"""Clean-site Frappe integration coverage for the Stage 1 foundation."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from erpnext_ua.consignment_and_commission.constants import (
    FOUNDATION_DOCTYPES,
    FOUNDATION_ROLES,
    FOUNDATION_WORKSPACES,
    SETTLEMENT_DOCTYPES,
)
from erpnext_ua.consignment_and_commission.services.diagnostics import (
    collect_environment,
)

COMPANY = "_CC Integration Company"
COMPANY_ABBR = "CCI"
SUPPLIER = "_CC Integration Supplier"
PARTNER = "_CC Integration Partner"
LOCATION = "_CC Integration Location"
SUPPLIER_GROUP = "_CC Integration Suppliers"
SUPPLIER_GROUP_ROOT = "_CC Integration All Suppliers"
_created_warehouse_type = False
_created_supplier_group_root = False
_original_multiple_item_settings: dict[str, int] = {}


def _enable_multiple_item_transactions() -> None:
    for doctype in ("Buying Settings", "Selling Settings"):
        if doctype not in _original_multiple_item_settings:
            _original_multiple_item_settings[doctype] = int(
                frappe.db.get_single_value(doctype, "allow_multiple_items") or 0
            )
        frappe.db.set_single_value(doctype, "allow_multiple_items", 1)


def _cleanup_integration_records() -> None:
    """Remove committed ERPNext setup records, even after a failed assertion."""
    global _created_supplier_group_root, _created_warehouse_type

    settings = frappe.get_single("CC Settings")
    if settings.default_company == COMPANY or settings.default_location == LOCATION:
        settings.enabled = 0
        settings.default_company = None
        settings.default_location = None
        settings.save(ignore_permissions=True)

    for name in frappe.get_all("CC Contract", filters={"company": COMPANY}, pluck="name"):
        frappe.delete_doc("CC Contract", name, force=True, ignore_permissions=True)
    for doctype, name in (
        ("CC Account Mapping", COMPANY),
        ("CC Partner Profile", PARTNER),
        ("CC Location", LOCATION),
    ):
        if frappe.db.exists(doctype, name):
            frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)

    supplier = frappe.db.get_value("Supplier", {"supplier_name": SUPPLIER}, "name")
    if supplier:
        frappe.delete_doc("Supplier", supplier, force=True, ignore_permissions=True)
    if frappe.db.exists("Supplier Group", SUPPLIER_GROUP):
        frappe.delete_doc("Supplier Group", SUPPLIER_GROUP, force=True, ignore_permissions=True)
    if _created_supplier_group_root and frappe.db.exists("Supplier Group", SUPPLIER_GROUP_ROOT):
        frappe.delete_doc("Supplier Group", SUPPLIER_GROUP_ROOT, force=True, ignore_permissions=True)
        _created_supplier_group_root = False
    warehouses = frappe.get_all(
        "Warehouse",
        filters={"company": COMPANY},
        fields=["name"],
        order_by="lft desc",
    )
    has_stock_ledger = bool(
        warehouses
        and frappe.db.exists(
            "Stock Ledger Entry",
            {"warehouse": ("in", [warehouse.name for warehouse in warehouses])},
        )
    )
    if not has_stock_ledger:
        for warehouse in warehouses:
            if frappe.db.exists("Warehouse", warehouse.name):
                frappe.delete_doc("Warehouse", warehouse.name, force=True, ignore_permissions=True)
    if not has_stock_ledger and frappe.db.exists("Company", COMPANY):
        frappe.delete_doc("Company", COMPANY, force=True, ignore_permissions=True)
    if _created_warehouse_type and frappe.db.exists("Warehouse Type", "Transit"):
        frappe.delete_doc("Warehouse Type", "Transit", force=True, ignore_permissions=True)
        _created_warehouse_type = False
    for doctype, original in tuple(_original_multiple_item_settings.items()):
        frappe.db.set_single_value(doctype, "allow_multiple_items", original)
        _original_multiple_item_settings.pop(doctype, None)
    frappe.db.commit()
    frappe.clear_cache()


def _ensure_company_dependencies() -> None:
    global _created_warehouse_type

    if not frappe.db.exists("Warehouse Type", "Transit"):
        frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert(ignore_permissions=True)
        _created_warehouse_type = True


def _ensure_company() -> frappe.model.document.Document:
    if frappe.db.exists("Company", COMPANY):
        return frappe.get_doc("Company", COMPANY)
    return frappe.get_doc(
        {
            "doctype": "Company",
            "company_name": COMPANY,
            "abbr": COMPANY_ABBR,
            "country": "United States",
            "default_currency": "USD",
            "create_chart_of_accounts_based_on": "Standard Template",
            "chart_of_accounts": "Standard",
        }
    ).insert(ignore_permissions=True)


def _ensure_warehouses() -> dict[str, str]:
    root_rows = frappe.get_all(
        "Warehouse",
        filters={"company": COMPANY, "is_group": 1},
        fields=["name"],
        order_by="lft asc",
        limit=1,
    )
    if not root_rows:
        raise AssertionError("Company creation did not produce a root Warehouse")

    group_name = frappe.db.get_value(
        "Warehouse",
        {"warehouse_name": "_CC Integration Stock", "company": COMPANY},
        "name",
    )
    group = (
        frappe.get_doc("Warehouse", group_name)
        if group_name
        else frappe.get_doc(
            {
                "doctype": "Warehouse",
                "warehouse_name": "_CC Integration Stock",
                "company": COMPANY,
                "parent_warehouse": root_rows[0].name,
                "is_group": 1,
            }
        ).insert(ignore_permissions=True)
    )

    warehouses: dict[str, str] = {}
    for stock_type in ("OWN", "COMMISSION", "CONSIGNMENT"):
        warehouse_name = frappe.db.get_value(
            "Warehouse",
            {"warehouse_name": f"_CC Integration {stock_type}", "company": COMPANY},
            "name",
        )
        warehouse = (
            frappe.get_doc("Warehouse", warehouse_name)
            if warehouse_name
            else frappe.get_doc(
                {
                    "doctype": "Warehouse",
                    "warehouse_name": f"_CC Integration {stock_type}",
                    "company": COMPANY,
                    "parent_warehouse": group.name,
                    "is_group": 0,
                }
            ).insert(ignore_permissions=True)
        )
        warehouses[stock_type] = warehouse.name
    return warehouses


def _ensure_supplier() -> frappe.model.document.Document:
    global _created_supplier_group_root

    root_rows = frappe.get_all(
        "Supplier Group",
        filters={"is_group": 1},
        fields=["name"],
        order_by="lft asc",
        limit=1,
    )
    if not root_rows:
        root = frappe.get_doc(
            {
                "doctype": "Supplier Group",
                "supplier_group_name": SUPPLIER_GROUP_ROOT,
                "is_group": 1,
            }
        ).insert(ignore_permissions=True)
        root_rows = [frappe._dict(name=root.name)]
        _created_supplier_group_root = True

    supplier_group = frappe.get_doc(
        {
            "doctype": "Supplier Group",
            "supplier_group_name": SUPPLIER_GROUP,
            "parent_supplier_group": root_rows[0].name,
            "is_group": 0,
        }
    ).insert(ignore_permissions=True)
    company = frappe.get_cached_doc("Company", COMPANY)
    supplier = frappe.get_doc(
        {
            "doctype": "Supplier",
            "supplier_name": SUPPLIER,
            "supplier_group": supplier_group.name,
            "supplier_type": "Company",
            "default_currency": company.default_currency,
            "accounts": [
                {
                    "company": COMPANY,
                    "account": company.default_payable_account,
                }
            ],
        }
    )
    return supplier.insert(ignore_permissions=True)


class TestFrappeFoundation(IntegrationTestCase):
    def test_clean_site_master_data_round_trip_and_overlap_guard(self) -> None:
        _cleanup_integration_records()
        self.addCleanup(_cleanup_integration_records)

        for doctype in FOUNDATION_DOCTYPES:
            self.assertTrue(frappe.db.exists("DocType", doctype), doctype)
        for role in FOUNDATION_ROLES:
            self.assertTrue(frappe.db.exists("Role", role), role)
        for workspace in FOUNDATION_WORKSPACES:
            self.assertTrue(frappe.db.exists("Workspace", workspace), workspace)
        for doctype in SETTLEMENT_DOCTYPES:
            self.assertTrue(frappe.db.exists("DocType", doctype), doctype)
        for report in (
            "CC FIFO Inventory",
            "CC Sale Financials",
            "CC Partner Balance",
            "CC POS Queue",
            "CC Financial Integrity",
        ):
            self.assertTrue(frappe.db.exists("Report", report), report)
        self.assertFalse(frappe.db.has_index("tabCC POS Route", "group_id"))
        readiness = collect_environment()
        self.assertEqual(readiness["status"], "ready_for_acceptance", readiness["blocking_checks"])

        _ensure_company_dependencies()
        company = _ensure_company()
        warehouses = _ensure_warehouses()
        supplier = _ensure_supplier()

        location = frappe.get_doc(
            {
                "doctype": "CC Location",
                "location_name": LOCATION,
                "company": COMPANY,
                "legal_entity_type": "Company",
                "legal_entity_name": COMPANY,
                "own_warehouse": warehouses["OWN"],
                "commission_warehouse": warehouses["COMMISSION"],
                "consignment_warehouse": warehouses["CONSIGNMENT"],
            }
        ).insert(ignore_permissions=True)
        self.assertEqual(location.legal_entity_label, COMPANY)

        partner = frappe.get_doc(
            {
                "doctype": "CC Partner Profile",
                "partner_name": PARTNER,
                "supplier": supplier.name,
                "allowed_relationship_models": "BOTH",
                "default_currency": company.default_currency,
                "default_settlement_deadline_days": 7,
            }
        ).insert(ignore_permissions=True)

        from erpnext_ua.consignment_and_commission.spikes.accounting import (
            _ensure_accounts,
        )

        accounts = _ensure_accounts(frappe, COMPANY, require_payment_accounts=False)
        mapping = frappe.get_doc(
            {
                "doctype": "CC Account Mapping",
                "company": COMPANY,
                "off_balance_goods_account": accounts["off_balance_goods"],
                "gross_proceeds_clearing_account": accounts["commission_gross_proceeds"],
                "commission_revenue_account": accounts["commission_revenue"],
                "principal_proceeds_deduction_account": accounts[
                    "principal_proceeds_deduction"
                ],
                "unreported_commission_liability_account": accounts[
                    "unreported_commission_liability"
                ],
                "unreported_consignment_liability_account": accounts[
                    "unreported_consignment_liability"
                ],
                "default_supplier_payable_account": accounts["supplier_payable"],
            }
        ).insert(ignore_permissions=True)
        self.assertEqual(mapping.name, COMPANY)

        settings = frappe.get_single("CC Settings")
        settings.update(
            {
                "enabled": 0,
                "enable_commission": 1,
                "enable_consignment": 1,
                "default_company": COMPANY,
                "default_location": location.name,
                "reservation_ttl_minutes": 15,
                "allocation_retry_limit": 3,
            }
        )
        settings.save(ignore_permissions=True)
        self.assertFalse(bool(settings.enabled))

        contract_values = {
            "doctype": "CC Contract",
            "status": "ACTIVE",
            "partner_profile": partner.name,
            "company": COMPANY,
            "location": location.name,
            "relationship_model": "COMMISSION",
            "currency": company.default_currency,
            "commission_rate": 15,
            "valid_from": nowdate(),
            "settlement_frequency": "MONTHLY",
            "settlement_deadline_days": 7,
            "fiscal_policy": "AUTO",
            "price_authority": "COMPANY",
        }
        contract = frappe.get_doc(
            contract_values | {"contract_title": "_CC Integration Commission Contract"}
        ).insert(ignore_permissions=True)
        self.assertEqual(contract.supplier, supplier.name)
        self.assertEqual(contract.legal_entity_name, COMPANY)

        with self.assertRaisesRegex(frappe.ValidationError, "overlaps CC Contract"):
            frappe.get_doc(
                contract_values | {"contract_title": "_CC Integration Overlapping Contract"}
            ).insert(ignore_permissions=True)
