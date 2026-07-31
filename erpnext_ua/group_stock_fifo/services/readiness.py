"""§30 readiness. The feature gate opens only when nothing blocks it."""

from __future__ import annotations

from typing import Any

from .domain import ReadinessReport

REQUIRED_ROLES = (
    "GSF Stock User",
    "GSF Stock Manager",
    "GSF Accountant",
    "GSF Auditor",
    "GSF System Manager",
    "GSF Manual Review Operator",
)


def readiness() -> ReadinessReport:
    """Assemble the §30.2 blocking checks and §30.3 warnings."""
    import frappe

    report = ReadinessReport()
    _check_platform(frappe, report)
    _check_roles(frappe, report)
    _check_groups(frappe, report)
    _check_warehouse_bindings(frappe, report)
    _check_lanes(frappe, report)
    _check_counterparty_dimension(frappe, report)
    _check_repeated_items(frappe, report)
    return report


def as_dict() -> dict[str, Any]:
    """Whitelist-friendly readiness payload (§30.4)."""
    import erpnext
    import frappe

    report = readiness()
    payload = report.as_dict()
    payload["environment"] = {
        "frappe_version": frappe.__version__,
        "erpnext_version": erpnext.__version__,
        "gsf_version": frappe.get_attr("erpnext_ua.__version__"),
    }
    return payload


def _check_platform(frappe: Any, report: ReadinessReport) -> None:
    import erpnext

    major = int(erpnext.__version__.split(".")[0])
    if major != 16:
        report.block(f"ERPNext major version is {major}, GSF requires 16")
    if not frappe.db.exists("DocType", "FOP Profile"):
        report.block("erpnext_ua UA FOP module is not installed")


def _check_roles(frappe: Any, report: ReadinessReport) -> None:
    missing = [role for role in REQUIRED_ROLES if not frappe.db.exists("Role", role)]
    if missing:
        report.block(f"Missing roles: {', '.join(missing)}")


def _check_groups(frappe: Any, report: ReadinessReport) -> None:
    groups = frappe.get_all(
        "GSF Company Group", filters={"enabled": 1}, fields=["name", "base_currency"]
    )
    if not groups:
        report.block("No enabled GSF Company Group exists")
        return

    for group in groups:
        members = frappe.get_all(
            "GSF Group Member",
            filters={"parent": group.name, "enabled": 1},
            fields=["company", "can_sell_stock", "can_source_stock"],
        )
        if not members:
            report.block(f"Company group {group.name} has no enabled members")
        for member in members:
            currency = frappe.db.get_value("Company", member.company, "default_currency")
            if currency != group.base_currency:
                report.block(
                    f"{member.company} base currency {currency} differs from "
                    f"group {group.name} currency {group.base_currency}"
                )
            _check_member_bindings(frappe, report, group.name, member)
            _check_clearing(frappe, report, group.name, member)


def _check_clearing(frappe: Any, report: ReadinessReport, group: str, member: Any) -> None:
    """ADR-005: reallocation cannot post without both balance-sheet accounts."""
    from .clearing import assert_balance_sheet
    from .domain import GSFError

    for fieldname in ("default_due_from_stock_account", "default_due_to_stock_account"):
        account = frappe.db.get_value(
            "GSF Group Member", {"parent": group, "company": member.company}, fieldname
        )
        if not account:
            report.block(
                f"{member.company} has no {fieldname.replace('_', ' ')} "
                f"(CLEARING_ACCOUNT_MISSING)"
            )
            continue
        try:
            assert_balance_sheet(account, company=member.company)
        except GSFError as error:
            report.block(str(error))


def _check_counterparty_dimension(frappe: Any, report: ReadinessReport) -> None:
    """A warning, not a block, and the reason is a platform limit.

    ADR-005 asks for a `Counterparty Accounting Company` accounting dimension
    and wants its absence to block. ERPNext 16 refuses to create an Accounting
    Dimension over `Company` at all, so blocking here would make the gate
    permanently unopenable. Reconciliation by counterparty runs off
    `GSF Reallocation Leg.counterparty_company` (§9.15) in the meantime.
    """
    from .clearing import COUNTERPARTY_DIMENSION, counterparty_dimension_field

    if not counterparty_dimension_field():
        report.warn(
            f"No {COUNTERPARTY_DIMENSION} accounting dimension; clearing balances are "
            "reconcilable only through GSF Reallocation Leg, not through GL reports"
        )


def _check_repeated_items(frappe: Any, report: ReadinessReport) -> None:
    """§18.3: one Item has to be allowed on several rows of one transaction.

    A managed sale splits a user line into one row per layer, and gate 0k showed
    that split is forced by the platform, not chosen. If Selling Settings
    collapses repeated items, §18.2 cannot be expressed at all — so this blocks.

    GSF does not flip the setting itself: §44 forbids changing a global ERPNext
    setting silently, and this one changes behaviour for every sale on the site,
    including the commission domain's.
    """
    if not frappe.db.exists("DocType", "Selling Settings"):
        return
    if frappe.db.get_single_value("Selling Settings", "allow_multiple_items"):
        return
    report.block(
        "Selling Settings does not allow one Item on several rows of a transaction; "
        "§18.2 technical rows cannot be created. Enable it deliberately, with an audit note."
    )


def _check_member_bindings(frappe: Any, report: ReadinessReport, group: str, member: Any) -> None:
    binding = frappe.db.exists(
        "GSF Location Company Binding",
        {"company_group": group, "company": member.company, "enabled": 1},
    )
    if member.can_source_stock and not binding:
        report.warn(f"{member.company} can source stock but has no active location binding")
    if not member.can_sell_stock:
        return
    lane = frappe.db.exists(
        "GSF Staging Lane", {"company": member.company, "enabled": 1}
    )
    if not lane:
        report.warn(f"{member.company} can sell but has no staging lane")


def _check_warehouse_bindings(frappe: Any, report: ReadinessReport) -> None:
    """§8.4: no overlap with another stock domain, no group warehouses."""
    overlaps = frappe.db.sql(
        """
        select gsf.warehouse
        from `tabGSF Warehouse Binding` gsf
        where gsf.enabled = 1 and gsf.manager_app = 'GSF'
          and exists (
            select 1 from `tabGSF Warehouse Binding` other
            where other.warehouse = gsf.warehouse and other.enabled = 1
              and other.manager_app != 'GSF'
          )
        """,
        pluck=True,
    )
    for warehouse in overlaps:
        report.block(f"Warehouse {warehouse} is bound to two stock domains")

    group_warehouses = frappe.db.sql(
        """
        select b.warehouse from `tabGSF Warehouse Binding` b
        join `tabWarehouse` w on w.name = b.warehouse
        where b.enabled = 1 and b.manager_app = 'GSF' and w.is_group = 1
        """,
        pluck=True,
    )
    for warehouse in group_warehouses:
        report.block(f"Warehouse {warehouse} is a group warehouse")


def _check_lanes(frappe: Any, report: ReadinessReport) -> None:
    """§8.4 and §30.2: no lane may be dirty or hold stock at activation."""
    dirty = frappe.get_all(
        "GSF Staging Lane", filters={"status": "DIRTY"}, fields=["name", "dirty_reason"]
    )
    for lane in dirty:
        report.block(f"Staging lane {lane.name} is dirty: {lane.dirty_reason or 'no reason given'}")

    lanes = frappe.get_all("GSF Staging Lane", filters={"enabled": 1}, pluck="warehouse")
    if not lanes:
        return
    held = frappe.db.sql(
        """
        select warehouse, item_code, actual_qty from `tabBin`
        where warehouse in %(lanes)s and actual_qty != 0
        """,
        {"lanes": tuple(lanes)},
        as_dict=True,
    )
    for row in held:
        report.block(
            f"Staging lane warehouse {row.warehouse} still holds "
            f"{row.actual_qty} of {row.item_code}"
        )
