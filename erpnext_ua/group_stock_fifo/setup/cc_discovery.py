"""Discover Consignment and Commission warehouses in the GSF domain registry.

CC remains the only writer for its warehouses.  GSF records read-only ownership
metadata so readiness, inventory counts, and overlap guards all use the same
warehouse registry without changing a ``CC Location``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

CC_MANAGER_APP = "CC"
CC_SOURCE_DOCTYPE = "CC Location"
DISCOVERED_EXTERNAL = "DISCOVERED_EXTERNAL"

CC_WAREHOUSE_ROLES = (
    ("own_warehouse", "CC_OWN"),
    ("commission_warehouse", "CC_COMMISSION"),
    ("consignment_warehouse", "CC_CONSIGNMENT"),
)


@dataclass(frozen=True, slots=True)
class CCLocationSnapshot:
    name: str
    company: str
    disabled: bool
    physical_location: str | None
    own_warehouse: str
    commission_warehouse: str
    consignment_warehouse: str


@dataclass(frozen=True, slots=True)
class DiscoveredBinding:
    warehouse: str
    company: str
    physical_location: str | None
    warehouse_role: str
    source_name: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class DiscoveryPlan:
    bindings: tuple[DiscoveredBinding, ...]
    conflicts: tuple[str, ...]


def plan_cc_bindings(locations: Iterable[CCLocationSnapshot]) -> DiscoveryPlan:
    """Return deterministic one-warehouse ownership and any ambiguous mappings."""
    discovered: dict[str, DiscoveredBinding] = {}
    ambiguous: set[str] = set()
    conflicts: list[str] = []

    for location in sorted(locations, key=lambda row: row.name):
        for fieldname, role in CC_WAREHOUSE_ROLES:
            warehouse = getattr(location, fieldname)
            if not warehouse:
                continue
            if warehouse in ambiguous:
                conflicts.append(
                    f"Warehouse {warehouse} has another ambiguous CC Location assignment"
                )
                continue
            candidate = DiscoveredBinding(
                warehouse=warehouse,
                company=location.company,
                physical_location=location.physical_location or None,
                warehouse_role=role,
                source_name=location.name,
                enabled=not location.disabled,
            )
            previous = discovered.get(warehouse)
            if previous and previous != candidate:
                conflicts.append(
                    f"Warehouse {warehouse} is assigned by both CC Location "
                    f"{previous.source_name} ({previous.warehouse_role}) and "
                    f"{candidate.source_name} ({candidate.warehouse_role})"
                )
                discovered.pop(warehouse, None)
                ambiguous.add(warehouse)
                continue
            discovered[warehouse] = candidate

    return DiscoveryPlan(
        bindings=tuple(discovered[name] for name in sorted(discovered)),
        conflicts=tuple(conflicts),
    )


def discover_cc_warehouses() -> dict[str, Any]:
    """Synchronize read-only CC ownership rows after install, migrate, or CC edits.

    An existing GSF binding is never overwritten.  The conflict stays visible
    to readiness and is logged, allowing migration to finish with the feature
    gate closed instead of mutating the wrong stock domain.
    """
    import frappe

    if not _schema_ready(frappe):
        return _result()

    plan = plan_cc_bindings(_location_snapshots(frappe))
    conflicts = list(plan.conflicts)
    desired = {binding.warehouse: binding for binding in plan.bindings}
    created = updated = disabled = 0

    for binding in plan.bindings:
        outcome, issue = _upsert_binding(frappe, binding)
        if issue:
            conflicts.append(issue)
        elif outcome == "created":
            created += 1
        elif outcome == "updated":
            updated += 1

    for row in _stale_bindings(frappe, desired):
        if row.enabled:
            frappe.db.set_value("GSF Warehouse Binding", row.name, "enabled", 0)
            disabled += 1

    _log_conflicts(frappe, conflicts)
    return _result(
        created=created,
        updated=updated,
        disabled=disabled,
        conflicts=conflicts,
    )


def audit_cc_bindings() -> list[str]:
    """Return blocking drift without writing, for the GSF readiness report."""
    import frappe

    if not _schema_ready(frappe):
        return []

    plan = plan_cc_bindings(_location_snapshots(frappe))
    issues = list(plan.conflicts)
    desired = {binding.warehouse: binding for binding in plan.bindings}

    for binding in plan.bindings:
        row = frappe.db.get_value(
            "GSF Warehouse Binding",
            binding.warehouse,
            [
                "manager_app",
                "company",
                "physical_location",
                "warehouse_role",
                "binding_mode",
                "source_doctype",
                "source_name",
                "enabled",
            ],
            as_dict=True,
        )
        if not row:
            issues.append(
                f"CC Warehouse {binding.warehouse} is missing its discovered domain binding"
            )
            continue
        if row.manager_app != CC_MANAGER_APP:
            issues.append(
                f"CC Warehouse {binding.warehouse} is bound to {row.manager_app} "
                "(CC_WAREHOUSE_CONFLICT)"
            )
            continue
        expected = _binding_values(frappe, binding)
        drifted = [
            fieldname
            for fieldname, value in expected.items()
            if str(row.get(fieldname) or "") != str(value or "")
        ]
        if drifted:
            issues.append(
                f"CC Warehouse {binding.warehouse} discovery is stale: "
                f"{', '.join(sorted(drifted))}"
            )

    for row in _stale_bindings(frappe, desired):
        if row.enabled:
            issues.append(
                f"CC Warehouse {row.warehouse} has an active stale discovered binding"
            )

    return issues


def _schema_ready(frappe: Any) -> bool:
    return bool(
        frappe.db.exists("DocType", CC_SOURCE_DOCTYPE)
        and frappe.db.exists("DocType", "GSF Warehouse Binding")
        and frappe.db.has_column(CC_SOURCE_DOCTYPE, "gsf_physical_location")
    )


def _location_snapshots(frappe: Any) -> list[CCLocationSnapshot]:
    rows = frappe.get_all(
        CC_SOURCE_DOCTYPE,
        fields=[
            "name",
            "company",
            "disabled",
            "gsf_physical_location",
            *(fieldname for fieldname, _role in CC_WAREHOUSE_ROLES),
        ],
    )
    return [
        CCLocationSnapshot(
            name=row.name,
            company=row.company,
            disabled=bool(row.disabled),
            physical_location=row.gsf_physical_location or None,
            own_warehouse=row.own_warehouse,
            commission_warehouse=row.commission_warehouse,
            consignment_warehouse=row.consignment_warehouse,
        )
        for row in rows
    ]


def _upsert_binding(frappe: Any, binding: DiscoveredBinding) -> tuple[str, str | None]:
    existing_app = frappe.db.get_value(
        "GSF Warehouse Binding", binding.warehouse, "manager_app"
    )
    if existing_app and existing_app != CC_MANAGER_APP:
        return "conflict", (
            f"CC Warehouse {binding.warehouse} is already bound to {existing_app} "
            "(CC_WAREHOUSE_CONFLICT)"
        )

    values = _binding_values(frappe, binding)
    if existing_app:
        doc = frappe.get_doc("GSF Warehouse Binding", binding.warehouse)
        changed = any(str(doc.get(key) or "") != str(value or "") for key, value in values.items())
        if not changed:
            return "unchanged", None
        doc.update(values)
        doc.save(ignore_permissions=True)
        return "updated", None

    frappe.get_doc(
        {
            "doctype": "GSF Warehouse Binding",
            "warehouse": binding.warehouse,
            **values,
        }
    ).insert(ignore_permissions=True)
    return "created", None


def _binding_values(frappe: Any, binding: DiscoveredBinding) -> dict[str, Any]:
    company_group = None
    if binding.physical_location:
        company_group = frappe.db.get_value(
            "GSF Physical Location", binding.physical_location, "company_group"
        )
    return {
        "company": binding.company,
        "company_group": company_group,
        "physical_location": binding.physical_location,
        "manager_app": CC_MANAGER_APP,
        "warehouse_role": binding.warehouse_role,
        "binding_mode": DISCOVERED_EXTERNAL,
        "source_doctype": CC_SOURCE_DOCTYPE,
        "source_name": binding.source_name,
        "enabled": int(binding.enabled),
    }


def _stale_bindings(frappe: Any, desired: dict[str, DiscoveredBinding]) -> list[Any]:
    rows = frappe.get_all(
        "GSF Warehouse Binding",
        filters={
            "manager_app": CC_MANAGER_APP,
            "binding_mode": DISCOVERED_EXTERNAL,
            "source_doctype": CC_SOURCE_DOCTYPE,
        },
        fields=["name", "warehouse", "source_name", "warehouse_role", "enabled"],
    )
    return [
        row
        for row in rows
        if row.warehouse not in desired
        or row.source_name != desired[row.warehouse].source_name
        or row.warehouse_role != desired[row.warehouse].warehouse_role
    ]


def _log_conflicts(frappe: Any, conflicts: list[str]) -> None:
    if not conflicts:
        return
    frappe.log_error(
        title="GSF CC warehouse discovery blocked",
        message="\n".join(sorted(set(conflicts))),
    )


def _result(
    *,
    created: int = 0,
    updated: int = 0,
    disabled: int = 0,
    conflicts: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "created": created,
        "updated": updated,
        "disabled": disabled,
        "conflicts": sorted(set(conflicts or [])),
    }
