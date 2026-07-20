from typing import Any

from ..constants import (
    APP_NAME,
    DIAGNOSTIC_DOCTYPES,
    DIAGNOSTIC_FIELDS,
    FOUNDATION_DOCTYPES,
    FOUNDATION_ROLES,
    FOUNDATION_WORKSPACES,
    OPTIONAL_APPS,
    REQUIRED_APPS,
    SETTLEMENT_DOCTYPES,
    STOCK_DOCTYPES,
    STOCK_SCHEMA_FIELDS,
)
from ..setup.ownership_dimension import (
    ALLOCATION_EXPIRY_INDEX,
    ALLOCATION_SERIAL_INDEX,
    BALANCE_INDEX,
    DIMENSION_NAME,
    POS_CHECKOUT_QUEUE_INDEX,
    POS_PRINT_QUEUE_INDEX,
    POS_ROUTE_QUEUE_INDEX,
    RETURN_AUDIT_INDEX,
    SALE_FINANCIAL_INDEX,
    SALE_REPORT_INDEX,
    SETTLEMENT_DUE_INDEX,
    STOCK_LOT_FIFO_INDEX,
)


def _doctype_exists(frappe: Any, doctype: str) -> bool:
    return bool(frappe.db.exists("DocType", doctype))


def _field_exists(frappe: Any, doctype: str, fieldname: str) -> bool:
    return bool(frappe.get_meta(doctype).has_field(fieldname))


def _psbo_data_checks(frappe: Any, *, feature_enabled: bool) -> list[dict[str, Any]]:
    """Detect configuration gaps and pre-1.1 records that need explicit valuation."""
    mapping_fields = (
        "off_balance_goods_account",
        "gross_proceeds_clearing_account",
        "commission_revenue_account",
        "principal_proceeds_deduction_account",
        "unreported_commission_liability_account",
        "unreported_consignment_liability_account",
        "default_supplier_payable_account",
    )
    schema_fields = {
        "CC Account Mapping": mapping_fields,
        "CC Receipt Item": (
            "accounting_unit_value",
            "accounting_amount",
            "off_balance_entry",
        ),
        "CC Stock Lot": (
            "off_balance_account",
            "off_balance_unit_value",
            "off_balance_amount",
            "off_balance_currency",
            "off_balance_entry",
        ),
        "CC Sale Allocation": ("off_balance_amount", "off_balance_entry"),
    }
    schema_ready = _doctype_exists(frappe, "UA Off Balance Entry") and all(
        _doctype_exists(frappe, doctype)
        and all(_field_exists(frappe, doctype, fieldname) for fieldname in fieldnames)
        for doctype, fieldnames in schema_fields.items()
    )
    if not schema_ready:
        return [
            {
                "kind": "psbo_configuration",
                "name": "CC Account Mapping.024/702/703/704/685",
                "required": feature_enabled,
                "present": False,
            },
            {
                "kind": "psbo_migration",
                "name": "account_024_schema",
                "required": True,
                "present": False,
                "unresolved_records": None,
            },
        ]
    active_companies = set(
        frappe.get_all(
            "CC Location",
            filters={"disabled": 0},
            pluck="company",
        )
    )
    mapped_companies = {
        row.company
        for row in frappe.get_all(
            "CC Account Mapping",
            fields=["company", *mapping_fields],
        )
        if all(row.get(fieldname) for fieldname in mapping_fields)
    }
    checks = [
        {
            "kind": "psbo_configuration",
            "name": "CC Account Mapping.024/702/703/704/685",
            "required": feature_enabled,
            "present": active_companies.issubset(mapped_companies),
        }
    ]

    migration_queries = {
        "CC Receipt.account_024": """
            select count(*)
            from `tabCC Receipt Item` item
            inner join `tabCC Receipt` receipt on receipt.name = item.parent
            inner join `tabCompany` company on company.name = receipt.company
            left join `tabUA Off Balance Entry` entry
                on entry.name = item.off_balance_entry and entry.docstatus = 1
            where receipt.docstatus = 1
              and (
                coalesce(item.accounting_unit_value, 0) <= 0
                or coalesce(item.accounting_amount, 0) <= 0
                or entry.name is null
                or coalesce(entry.currency, '') != company.default_currency
              )
        """,
        "CC Stock Lot.account_024_snapshot": """
            select count(*)
            from `tabCC Stock Lot` lot
            inner join `tabCompany` company on company.name = lot.company
            where lot.lot_status != 'CANCELLED'
              and lot.relationship_model in ('COMMISSION', 'CONSIGNMENT')
              and (
                coalesce(lot.off_balance_account, '') = ''
                or coalesce(lot.off_balance_unit_value, 0) <= 0
                or coalesce(lot.off_balance_amount, 0) <= 0
                or coalesce(lot.off_balance_currency, '') = ''
                or coalesce(lot.off_balance_currency, '') != company.default_currency
                or coalesce(lot.off_balance_entry, '') = ''
              )
        """,
        "CC Sale Allocation.account_024_exit": """
            select count(*)
            from `tabCC Sale Allocation` allocation
            inner join `tabSales Invoice` invoice
                on invoice.name = allocation.sales_invoice and invoice.docstatus = 1
            where allocation.relationship_model in ('COMMISSION', 'CONSIGNMENT')
              and (
                coalesce(allocation.off_balance_amount, 0) <= 0
                or coalesce(allocation.off_balance_entry, '') = ''
              )
        """,
    }
    for name, query in migration_queries.items():
        unresolved = int(frappe.db.sql(query)[0][0])
        checks.append(
            {
                "kind": "psbo_migration",
                "name": name,
                "required": True,
                "present": unresolved == 0,
                "unresolved_records": unresolved,
            }
        )
    return checks


def collect_environment() -> dict[str, Any]:
    """Collect read-only production-readiness schema and configuration evidence.

    The function deliberately performs no writes and does not submit test
    transactions. Transactional probes belong to the explicit Stage 0 runner.
    """
    import frappe

    installed_apps = frozenset(frappe.get_installed_apps())
    feature_enabled = bool(frappe.db.get_single_value("CC Settings", "enabled"))

    app_checks = [
        {
            "kind": "app",
            "name": app_name,
            "required": app_name in REQUIRED_APPS,
            "present": app_name in installed_apps,
        }
        for app_name in sorted(REQUIRED_APPS | OPTIONAL_APPS)
    ]

    doctype_checks = [
        {"kind": "doctype", "name": doctype, "required": True, "present": _doctype_exists(frappe, doctype)}
        for doctype in DIAGNOSTIC_DOCTYPES
    ]

    foundation_doctype_checks = [
        {"kind": "foundation_doctype", "name": doctype, "required": True, "present": _doctype_exists(frappe, doctype)}
        for doctype in FOUNDATION_DOCTYPES
    ]

    stock_doctype_checks = [
        {"kind": "stock_doctype", "name": doctype, "required": True, "present": _doctype_exists(frappe, doctype)}
        for doctype in STOCK_DOCTYPES
    ]

    settlement_doctype_checks = [
        {
            "kind": "settlement_doctype",
            "name": doctype,
            "required": True,
            "present": _doctype_exists(frappe, doctype),
        }
        for doctype in SETTLEMENT_DOCTYPES
    ]

    role_checks = [
        {
            "kind": "role",
            "name": role,
            "required": True,
            "present": bool(frappe.db.exists("Role", role)),
        }
        for role in FOUNDATION_ROLES
    ]

    workspace_checks = [
        {
            "kind": "workspace",
            "name": workspace,
            "required": True,
            "present": bool(frappe.db.exists("Workspace", workspace)),
        }
        for workspace in FOUNDATION_WORKSPACES
    ]

    field_checks = [
        {
            "kind": "field",
            "name": f"{doctype}.{fieldname}",
            "required": True,
            "present": _doctype_exists(frappe, doctype) and _field_exists(frappe, doctype, fieldname),
        }
        for doctype, fieldnames in DIAGNOSTIC_FIELDS.items()
        for fieldname in fieldnames
    ]

    stock_field_checks = [
        {
            "kind": "stock_field",
            "name": f"{doctype}.{fieldname}",
            "required": True,
            "present": _doctype_exists(frappe, doctype) and _field_exists(frappe, doctype, fieldname),
        }
        for doctype, fieldnames in STOCK_SCHEMA_FIELDS.items()
        for fieldname in fieldnames
    ]

    index_checks = (
        ("Stock Ledger Entry", BALANCE_INDEX),
        ("CC Allocation", ALLOCATION_EXPIRY_INDEX),
        ("CC Allocation Slice", ALLOCATION_SERIAL_INDEX),
        ("CC Stock Lot", STOCK_LOT_FIFO_INDEX),
        ("CC Sale Allocation", SALE_FINANCIAL_INDEX),
        ("CC Sale Allocation", SALE_REPORT_INDEX),
        ("CC Sale Return Allocation", RETURN_AUDIT_INDEX),
        ("CC Settlement Report", SETTLEMENT_DUE_INDEX),
        ("CC POS Checkout", POS_CHECKOUT_QUEUE_INDEX),
        ("CC POS Route", POS_ROUTE_QUEUE_INDEX),
        ("CC POS Print Job", POS_PRINT_QUEUE_INDEX),
    )
    ownership_checks = [
        {
            "kind": "inventory_dimension",
            "name": DIMENSION_NAME,
            "required": True,
            "present": bool(frappe.db.exists("Inventory Dimension", DIMENSION_NAME)),
        },
        *[
            {
                "kind": "database_index",
                "name": f"{doctype}.{index_name}",
                "required": True,
                "present": _doctype_exists(frappe, doctype)
                and bool(frappe.db.has_index(f"tab{doctype}", index_name)),
            }
            for doctype, index_name in index_checks
        ],
        {
            "kind": "configuration",
            "name": "Stock Settings.enable_serial_and_batch_no_for_item",
            "required": False,
            "present": bool(
                frappe.db.get_single_value(
                    "Stock Settings",
                    "enable_serial_and_batch_no_for_item",
                )
            ),
        },
        *[
            {
                "kind": "configuration",
                "name": f"{doctype}.allow_multiple_items",
                "required": feature_enabled,
                "present": bool(
                    frappe.db.get_single_value(
                        doctype,
                        "allow_multiple_items",
                    )
                ),
            }
            for doctype in ("Buying Settings", "Selling Settings")
        ],
        {
            "kind": "configuration",
            "name": "Price List.enabled_selling",
            "required": feature_enabled,
            "present": bool(
                frappe.db.exists(
                    "Price List",
                    {"enabled": 1, "selling": 1},
                )
            ),
        },
        *[
            {
                "kind": "configuration",
                "name": f"Party Type.{party_type}",
                "required": feature_enabled,
                "present": frappe.db.get_value(
                    "Party Type",
                    party_type,
                    "account_type",
                )
                == account_type,
            }
            for party_type, account_type in (
                ("Customer", "Receivable"),
                ("Supplier", "Payable"),
            )
        ],
    ]

    psbo_checks = _psbo_data_checks(frappe, feature_enabled=feature_enabled)

    checks = (
        app_checks
        + doctype_checks
        + field_checks
        + foundation_doctype_checks
        + stock_doctype_checks
        + settlement_doctype_checks
        + stock_field_checks
        + ownership_checks
        + role_checks
        + workspace_checks
        + psbo_checks
    )
    blocking = [check for check in checks if check["required"] and not check["present"]]

    return {
        "app": APP_NAME,
        "stage": 7,
        "status": "blocked" if blocking else "ready_for_acceptance",
        "installed_apps": sorted(installed_apps),
        "checks": checks,
        "blocking_checks": blocking,
    }
