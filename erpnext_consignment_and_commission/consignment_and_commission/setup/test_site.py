"""Guarded, idempotent Stage 1 master-data bootstrap for postest.local."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..spikes.accounting import _ensure_accounts
from ..spikes.fifo import _ensure_location_warehouses

ALLOWED_SITE = "postest.local"
COMPANY = "POS Test Ukraine"
CONFIRMATION = "SEED_STAGE_1_FOUNDATION"
LOCATION = "CC Test Main Location"
SUPPLIER = "CC Test Partner Supplier UAH"
PARTNER = "CC Test Partner UAH"
COMMISSION_CONTRACT = "CC Test Commission Contract"
CONSIGNMENT_CONTRACT = "CC Test Consignment Contract"
VALID_FROM = "2026-07-13"


def _assert_scope(frappe: Any, *, confirm_site: str, confirm_write: str, company: str) -> None:
    if frappe.local.site != ALLOWED_SITE or confirm_site != ALLOWED_SITE:
        raise RuntimeError(f"Stage 1 bootstrap is restricted to {ALLOWED_SITE}")
    if confirm_write != CONFIRMATION:
        raise RuntimeError(f"Explicit confirmation {CONFIRMATION!r} is required")
    if company != COMPANY:
        raise RuntimeError(f"Stage 1 bootstrap is restricted to Company {COMPANY!r}")


def _normalize_field(value: Any) -> str | Decimal:
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    return str(value or "")


def _assert_fields(document: Any, expected: dict[str, Any], label: str) -> None:
    mismatches = {
        fieldname: {"expected": value, "actual": document.get(fieldname)}
        for fieldname, value in expected.items()
        if _normalize_field(document.get(fieldname)) != _normalize_field(value)
    }
    if mismatches:
        raise RuntimeError(f"Existing {label} is incompatible with the Stage 1 fixture: {mismatches}")


def _ensure_supplier(frappe: Any, *, company: str, payable_account: str) -> tuple[str, bool]:
    existing = frappe.db.get_value("Supplier", {"supplier_name": SUPPLIER}, "name")
    currency = frappe.db.get_value("Account", payable_account, "account_currency")
    if existing:
        supplier = frappe.get_doc("Supplier", existing)
        _assert_fields(
            supplier,
            {
                "supplier_name": SUPPLIER,
                "supplier_type": "Company",
                "default_currency": currency,
            },
            f"Supplier {existing}",
        )
        company_rows = [row for row in supplier.get("accounts") if row.company == company]
        if len(company_rows) != 1 or company_rows[0].account != payable_account:
            raise RuntimeError(f"Existing Supplier {existing!r} has incompatible payable-account routing")
        return existing, False

    supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
    if not supplier_group:
        raise RuntimeError("A leaf Supplier Group is required for the Stage 1 test fixture")
    supplier = frappe.get_doc(
        {
            "doctype": "Supplier",
            "supplier_name": SUPPLIER,
            "supplier_type": "Company",
            "supplier_group": supplier_group,
            "default_currency": currency,
            "accounts": [{"company": company, "account": payable_account}],
        }
    ).insert(ignore_permissions=True)
    return supplier.name, True


def _ensure_location(
    frappe: Any,
    *,
    company: str,
    warehouses: dict[str, str],
) -> tuple[str, bool]:
    expected = {
        "location_name": LOCATION,
        "company": company,
        "disabled": 0,
        "legal_entity_type": "Company",
        "legal_entity_name": company,
        "own_warehouse": warehouses["OWN"],
        "commission_warehouse": warehouses["COMMISSION"],
        "consignment_warehouse": warehouses["CONSIGNMENT"],
    }
    if frappe.db.exists("CC Location", LOCATION):
        location = frappe.get_doc("CC Location", LOCATION)
        _assert_fields(location, expected, f"CC Location {LOCATION}")
        location.run_method("validate")
        return location.name, False
    location = frappe.get_doc({"doctype": "CC Location", **expected}).insert(ignore_permissions=True)
    return location.name, True


def _ensure_partner(
    frappe: Any,
    *,
    supplier: str,
    currency: str,
) -> tuple[str, bool]:
    expected = {
        "partner_name": PARTNER,
        "supplier": supplier,
        "disabled": 0,
        "allowed_relationship_models": "BOTH",
        "default_currency": currency,
        "default_settlement_deadline_days": 7,
    }
    if frappe.db.exists("CC Partner Profile", PARTNER):
        partner = frappe.get_doc("CC Partner Profile", PARTNER)
        _assert_fields(partner, expected, f"CC Partner Profile {PARTNER}")
        partner.run_method("validate")
        return partner.name, False
    partner = frappe.get_doc({"doctype": "CC Partner Profile", **expected}).insert(ignore_permissions=True)
    return partner.name, True


def _ensure_mapping(
    frappe: Any,
    *,
    company: str,
    accounts: dict[str, str],
) -> tuple[str, bool]:
    expected = {
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
    if frappe.db.exists("CC Account Mapping", company):
        mapping = frappe.get_doc("CC Account Mapping", company)
        _assert_fields(mapping, expected, f"CC Account Mapping {company}")
        mapping.run_method("validate")
        return mapping.name, False
    mapping = frappe.get_doc({"doctype": "CC Account Mapping", **expected}).insert(ignore_permissions=True)
    return mapping.name, True


def _ensure_contract(
    frappe: Any,
    *,
    title: str,
    model: str,
    partner: str,
    company: str,
    location: str,
    currency: str,
) -> tuple[str, bool]:
    expected = {
        "contract_title": title,
        "status": "DRAFT",
        "partner_profile": partner,
        "company": company,
        "location": location,
        "relationship_model": model,
        "currency": currency,
        "commission_rate": 15 if model == "COMMISSION" else 0,
        "valid_from": VALID_FROM,
        "settlement_frequency": "MONTHLY",
        "settlement_deadline_days": 7,
        "fiscal_policy": "AUTO",
        "price_authority": "COMPANY",
    }
    matches = frappe.get_all("CC Contract", filters={"contract_title": title}, pluck="name")
    if len(matches) > 1:
        raise RuntimeError(f"Multiple CC Contracts use reserved test title {title!r}: {matches}")
    if matches:
        contract = frappe.get_doc("CC Contract", matches[0])
        _assert_fields(contract, expected, f"CC Contract {matches[0]}")
        contract.run_method("validate")
        return contract.name, False
    contract = frappe.get_doc({"doctype": "CC Contract", **expected}).insert(ignore_permissions=True)
    return contract.name, True


def _ensure_settings(frappe: Any, *, company: str, location: str) -> tuple[str, bool]:
    settings = frappe.get_single("CC Settings")
    expected = {
        "enabled": 0,
        "enable_commission": 1,
        "enable_consignment": 1,
        "default_company": company,
        "default_location": location,
        "reservation_ttl_minutes": 15,
        "allocation_retry_limit": 3,
    }
    configured = bool(settings.default_company or settings.default_location)
    if configured:
        _assert_fields(settings, expected, "CC Settings")
        settings.run_method("validate")
        return settings.name, False
    settings.update(expected)
    settings.save(ignore_permissions=True)
    return settings.name, True


def bootstrap_stage_1(
    confirm_site: str,
    confirm_write: str,
    company: str = COMPANY,
) -> dict[str, Any]:
    """Create reserved test masters without enabling transaction hooks."""
    import frappe

    _assert_scope(frappe, confirm_site=confirm_site, confirm_write=confirm_write, company=company)
    if not frappe.db.exists("Company", company):
        raise RuntimeError(f"Required test Company {company!r} does not exist")

    created: list[str] = []
    existing: list[str] = []

    def record(label: str, was_created: bool) -> None:
        (created if was_created else existing).append(label)

    try:
        _, warehouses = _ensure_location_warehouses(frappe, company)
        accounts = _ensure_accounts(frappe, company, require_payment_accounts=False)
        currency = frappe.get_cached_value("Company", company, "default_currency")

        supplier, was_created = _ensure_supplier(
            frappe,
            company=company,
            payable_account=accounts["supplier_payable"],
        )
        record(f"Supplier:{supplier}", was_created)
        location, was_created = _ensure_location(frappe, company=company, warehouses=warehouses)
        record(f"CC Location:{location}", was_created)
        partner, was_created = _ensure_partner(frappe, supplier=supplier, currency=currency)
        record(f"CC Partner Profile:{partner}", was_created)
        mapping, was_created = _ensure_mapping(frappe, company=company, accounts=accounts)
        record(f"CC Account Mapping:{mapping}", was_created)

        contracts: dict[str, str] = {}
        for model, title in (
            ("COMMISSION", COMMISSION_CONTRACT),
            ("CONSIGNMENT", CONSIGNMENT_CONTRACT),
        ):
            contract, was_created = _ensure_contract(
                frappe,
                title=title,
                model=model,
                partner=partner,
                company=company,
                location=location,
                currency=currency,
            )
            contracts[model] = contract
            record(f"CC Contract:{contract}", was_created)

        settings, was_created = _ensure_settings(frappe, company=company, location=location)
        record(f"CC Settings:{settings}", was_created)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        raise

    return {
        "site": frappe.local.site,
        "company": company,
        "feature_enabled": bool(frappe.db.get_single_value("CC Settings", "enabled")),
        "created": created,
        "existing": existing,
        "records": {
            "location": location,
            "supplier": supplier,
            "partner": partner,
            "account_mapping": mapping,
            "contracts": contracts,
            "settings": settings,
        },
    }
