"""Phase 0 fixtures: the HUNTER.rv shared pool of three FOP companies.

Test-site only. Creates what the wave 1 gates need and nothing else: three
companies with FOP Profile, one pool warehouse and one Sale Stage warehouse per
company, and a single stock item. The `gsf_stock_layer` Inventory Dimension is
deliberately left to the gate that needs it (0d), so 0b and 0e can run against
an unchanged schema.

    bench --site postest.local execute \\
      erpnext_ua.group_stock_fifo.spikes.fixtures.build \\
      --kwargs '{"confirm_site":"postest.local","confirm_write":"BUILD_GSF_PHASE_0"}'
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

CONFIRMATION = "BUILD_GSF_PHASE_0"
TEARDOWN_CONFIRMATION = "DROP_GSF_PHASE_0"
ALLOWED_SITES = frozenset({"postest.local", "postest-restore.local"})

LOCATION = "HUNTER.rv м.Рівне"
POOL_WAREHOUSE = "HUNTER.rv Пул"
STAGE_WAREHOUSE = "HUNTER.rv Комплектування"
ITEM_CODE = "GSF-PHASE0-ITEM"
CREATED_COMPANIES_KEY = "gsf_phase0_created_companies"


@dataclass(frozen=True, slots=True)
class FopFixture:
    company: str
    abbr: str
    tax_id: str
    single_tax_group: str
    tax_rate_mode: str

    @property
    def pool_warehouse(self) -> str:
        return f"{POOL_WAREHOUSE} - {self.abbr}"

    @property
    def stage_warehouse(self) -> str:
        return f"{STAGE_WAREHOUSE} - {self.abbr}"


# РНОКПП синтетичні: префікс 999999900* з правильною контрольною сумою.
# Реальні номери живих людей у фікстури не потрапляють.
FOPS = (
    FopFixture("ФОП Козярчук Роман Вячеславович", "ФКРВ", "9999999008", "3", "5% без ПДВ"),
    FopFixture("ФОП Козярчук Вячеслав Володимирович", "ФКВВ", "9999999014", "2", "Фіксована ставка"),
    FopFixture("ФОП Кравчук Іван Валентинович", "ФКІВ", "9999999020", "3", "3% з ПДВ"),
)


def build(confirm_site: str, confirm_write: str) -> dict[str, Any]:
    """Create the three-FOP pool. Idempotent: existing records are reused."""
    import frappe

    assert_scope(frappe, confirm_site=confirm_site, confirm_write=confirm_write, expected=CONFIRMATION)
    report = _Builder(frappe).run()
    frappe.db.commit()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def state(confirm_site: str) -> dict[str, Any]:
    """Read-only view of what the fixture currently owns on this site."""
    import frappe

    assert_site(frappe, confirm_site)
    report = {
        "site": frappe.local.site,
        "location": LOCATION,
        "item": bool(frappe.db.exists("Item", ITEM_CODE)),
        "created_companies": _created_companies(frappe),
        "fops": [
            {
                "company": fop.company,
                "company_exists": bool(frappe.db.exists("Company", fop.company)),
                "fop_profile": bool(frappe.db.exists("FOP Profile", fop.company)),
                "pool_warehouse": bool(frappe.db.exists("Warehouse", fop.pool_warehouse)),
                "stage_warehouse": bool(frappe.db.exists("Warehouse", fop.stage_warehouse)),
            }
            for fop in FOPS
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def teardown(confirm_site: str, confirm_write: str) -> dict[str, Any]:
    """Remove the fixture. Refuses to touch a company that carries stock."""
    import frappe

    assert_scope(frappe, confirm_site=confirm_site, confirm_write=confirm_write, expected=TEARDOWN_CONFIRMATION)
    report = _Teardown(frappe).run()
    frappe.db.commit()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


class _Builder:
    def __init__(self, frappe: Any) -> None:
        self.frappe = frappe
        self.created: list[str] = []
        self.reused: list[str] = []

    def run(self) -> dict[str, Any]:
        new_companies = []
        for fop in FOPS:
            if self._ensure_company(fop):
                new_companies.append(fop.company)
            self._ensure_fop_profile(fop)
            self._ensure_warehouse(fop, POOL_WAREHOUSE, fop.pool_warehouse)
            self._ensure_warehouse(fop, STAGE_WAREHOUSE, fop.stage_warehouse)
        self._ensure_item()
        self._remember_created_companies(new_companies)
        return {
            "site": self.frappe.local.site,
            "location": LOCATION,
            "created": self.created,
            "reused": self.reused,
            "companies_created_by_fixture": _created_companies(self.frappe),
        }

    def _ensure_company(self, fop: FopFixture) -> bool:
        if self.frappe.db.exists("Company", fop.company):
            self.reused.append(f"Company {fop.company}")
            return False
        template = self.frappe.db.get_value(
            "Company",
            FOPS[0].company,
            ["chart_of_accounts", "create_chart_of_accounts_based_on", "enable_perpetual_inventory"],
            as_dict=True,
        ) or {}
        self.frappe.get_doc(
            {
                "doctype": "Company",
                "company_name": fop.company,
                "abbr": fop.abbr,
                "default_currency": "UAH",
                "country": "Ukraine",
                "chart_of_accounts": template.get("chart_of_accounts") or "Standard",
                "create_chart_of_accounts_based_on": template.get("create_chart_of_accounts_based_on")
                or "Standard Template",
                "enable_perpetual_inventory": template.get("enable_perpetual_inventory", 1),
            }
        ).insert(ignore_permissions=True)
        self.created.append(f"Company {fop.company}")
        return True

    def _ensure_fop_profile(self, fop: FopFixture) -> None:
        # FOP Profile is named after its company, so the link is the key.
        if self.frappe.db.exists("FOP Profile", fop.company):
            self.reused.append(f"FOP Profile {fop.company}")
            return
        self.frappe.get_doc(
            {
                "doctype": "FOP Profile",
                "company": fop.company,
                "fop_full_name": fop.company.removeprefix("ФОП ").strip(),
                "prro_registered_name": fop.company,
                "tax_id": fop.tax_id,
                "status": "Active",
                "single_tax_group": fop.single_tax_group,
                "tax_rate_mode": fop.tax_rate_mode,
                "registration_address": LOCATION,
            }
        ).insert(ignore_permissions=True)
        self.created.append(f"FOP Profile {fop.company}")

    def _ensure_warehouse(self, fop: FopFixture, warehouse_name: str, full_name: str) -> None:
        if self.frappe.db.exists("Warehouse", full_name):
            self.reused.append(f"Warehouse {full_name}")
            return
        self.frappe.get_doc(
            {
                "doctype": "Warehouse",
                "warehouse_name": warehouse_name,
                "company": fop.company,
                "parent_warehouse": f"All Warehouses - {fop.abbr}",
                "is_group": 0,
            }
        ).insert(ignore_permissions=True)
        self.created.append(f"Warehouse {full_name}")

    def _ensure_item(self) -> None:
        if self.frappe.db.exists("Item", ITEM_CODE):
            self.reused.append(f"Item {ITEM_CODE}")
            return
        item_group = self.frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
        self.frappe.get_doc(
            {
                "doctype": "Item",
                "item_code": ITEM_CODE,
                "item_name": "GSF Phase 0 товар",
                "item_group": item_group,
                "stock_uom": "Nos",
                "is_stock_item": 1,
                "is_purchase_item": 1,
                "is_sales_item": 1,
            }
        ).insert(ignore_permissions=True)
        self.created.append(f"Item {ITEM_CODE}")

    def _remember_created_companies(self, new_companies: list[str]) -> None:
        if not new_companies:
            return
        known = set(_created_companies(self.frappe)) | set(new_companies)
        self.frappe.db.set_global(CREATED_COMPANIES_KEY, json.dumps(sorted(known), ensure_ascii=False))


class _Teardown:
    def __init__(self, frappe: Any) -> None:
        self.frappe = frappe
        self.removed: list[str] = []
        self.kept: list[str] = []

    def run(self) -> dict[str, Any]:
        self._drop_item()
        for fop in FOPS:
            self._drop_warehouse(fop.pool_warehouse)
            self._drop_warehouse(fop.stage_warehouse)
            self._drop_doc("FOP Profile", fop.company)
        self._drop_companies()
        return {"site": self.frappe.local.site, "removed": self.removed, "kept": self.kept}

    def _drop_companies(self) -> None:
        # Only companies this fixture created; a pre-existing company stays.
        for company in _created_companies(self.frappe):
            if self.frappe.db.count("Stock Ledger Entry", {"company": company}):
                self.kept.append(f"Company {company} (has stock ledger entries)")
                continue
            self._drop_doc("Company", company)
        self.frappe.db.set_global(CREATED_COMPANIES_KEY, None)

    def _drop_item(self) -> None:
        if self.frappe.db.count("Stock Ledger Entry", {"item_code": ITEM_CODE}):
            self.kept.append(f"Item {ITEM_CODE} (has stock ledger entries)")
            return
        self._drop_doc("Item", ITEM_CODE)

    def _drop_warehouse(self, name: str) -> None:
        if self.frappe.db.count("Stock Ledger Entry", {"warehouse": name}):
            self.kept.append(f"Warehouse {name} (has stock ledger entries)")
            return
        self._drop_doc("Warehouse", name)

    def _drop_doc(self, doctype: str, name: str) -> None:
        if not self.frappe.db.exists(doctype, name):
            return
        self.frappe.delete_doc(doctype, name, force=True, ignore_permissions=True, delete_permanently=True)
        self.removed.append(f"{doctype} {name}")


def _created_companies(frappe: Any) -> list[str]:
    raw = frappe.db.get_global(CREATED_COMPANIES_KEY)
    return json.loads(raw) if raw else []


def assert_scope(frappe: Any, *, confirm_site: str, confirm_write: str, expected: str) -> None:
    assert_site(frappe, confirm_site)
    if confirm_write != expected:
        raise RuntimeError(f"Explicit confirmation {expected!r} is required")


def assert_site(frappe: Any, confirm_site: str) -> None:
    if frappe.local.site not in ALLOWED_SITES or confirm_site != frappe.local.site:
        raise RuntimeError("GSF Phase 0 fixtures are restricted to an allow-listed test site")
