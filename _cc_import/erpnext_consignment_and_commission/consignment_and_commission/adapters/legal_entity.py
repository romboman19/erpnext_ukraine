from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class EntityRef:
    entity_type: str
    entity_name: str
    company: str
    display_name: str
    dimensions: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class LegalEntityAdapter(Protocol):
    def list_entities(self, company: str, user: str) -> list[EntityRef]: ...

    def validate_entity(self, company: str, entity: EntityRef) -> None: ...

    def accounting_dimension_values(self, entity: EntityRef) -> dict[str, str]: ...

    def default_accounts(self, entity: EntityRef, purpose: str) -> dict[str, str]: ...

    def payment_accounts(self, entity: EntityRef) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class FOPProfileRecord:
    name: str
    company: str
    display_name: str
    status: str


@runtime_checkable
class LegalEntityStore(Protocol):
    def has_doctype(self, doctype: str) -> bool: ...

    def list_fop_profiles(self, company: str, user: str) -> list[FOPProfileRecord]: ...

    def get_fop_profile(self, name: str, user: str) -> FOPProfileRecord | None: ...


class FrappeLegalEntityStore:
    """Read optional legal-entity records without importing another app."""

    def __init__(self, frappe_module: Any | None = None) -> None:
        if frappe_module is None:
            import frappe

            frappe_module = frappe
        self.frappe = frappe_module

    def has_doctype(self, doctype: str) -> bool:
        return bool(self.frappe.db.exists("DocType", doctype))

    def list_fop_profiles(self, company: str, user: str) -> list[FOPProfileRecord]:
        del user
        if not self.has_doctype("FOP Profile"):
            return []
        rows = self.frappe.get_list(
            "FOP Profile",
            filters={"company": company, "status": "Active"},
            fields=["name", "company", "fop_full_name", "status"],
            order_by="name",
        )
        return [
            FOPProfileRecord(row.name, row.company, row.fop_full_name or row.name, row.status) for row in rows
        ]

    def get_fop_profile(self, name: str, user: str) -> FOPProfileRecord | None:
        if not self.has_doctype("FOP Profile") or not self.frappe.has_permission(
            "FOP Profile", ptype="read", doc=name, user=user
        ):
            return None
        row = self.frappe.db.get_value(
            "FOP Profile",
            name,
            ["name", "company", "fop_full_name", "status"],
            as_dict=True,
        )
        if not row:
            return None
        return FOPProfileRecord(row.name, row.company, row.fop_full_name or row.name, row.status)


class CompanyLegalEntityAdapter:
    def list_entities(self, company: str, user: str) -> list[EntityRef]:
        del user
        return [EntityRef("Company", company, company, company)]

    def validate_entity(self, company: str, entity: EntityRef) -> None:
        if entity.entity_type != "Company" or entity.entity_name != company or entity.company != company:
            raise ValueError("Company legal entity must match the location company")

    def accounting_dimension_values(self, entity: EntityRef) -> dict[str, str]:
        self.validate_entity(entity.company, entity)
        return {}

    def default_accounts(self, entity: EntityRef, purpose: str) -> dict[str, str]:
        del purpose
        self.validate_entity(entity.company, entity)
        return {}

    def payment_accounts(self, entity: EntityRef) -> list[str]:
        self.validate_entity(entity.company, entity)
        return []


class FOPProfileLegalEntityAdapter:
    def __init__(self, store: LegalEntityStore) -> None:
        self.store = store

    def list_entities(self, company: str, user: str) -> list[EntityRef]:
        return [
            EntityRef("FOP Profile", row.name, row.company, row.display_name)
            for row in self.store.list_fop_profiles(company, user)
        ]

    def validate_entity(self, company: str, entity: EntityRef) -> None:
        if entity.entity_type != "FOP Profile" or entity.company != company:
            raise ValueError("FOP Profile legal entity must match the location company")
        if not entity.display_name:
            raise ValueError("FOP Profile legal entity requires a display name")

    def accounting_dimension_values(self, entity: EntityRef) -> dict[str, str]:
        self.validate_entity(entity.company, entity)
        return {}

    def default_accounts(self, entity: EntityRef, purpose: str) -> dict[str, str]:
        del purpose
        self.validate_entity(entity.company, entity)
        return {}

    def payment_accounts(self, entity: EntityRef) -> list[str]:
        self.validate_entity(entity.company, entity)
        return []


def get_legal_entity_adapter(
    entity_type: str,
    store: LegalEntityStore | None = None,
) -> LegalEntityAdapter:
    if entity_type == "Company":
        return CompanyLegalEntityAdapter()
    if entity_type == "FOP Profile":
        store = store or FrappeLegalEntityStore()
        if not store.has_doctype("FOP Profile"):
            raise ValueError("FOP Profile is unavailable because the optional DocType is not installed")
        return FOPProfileLegalEntityAdapter(store)
    raise ValueError(f"Unsupported legal entity type: {entity_type}")


def resolve_legal_entity(
    company: str,
    entity_type: str,
    entity_name: str,
    user: str,
    store: LegalEntityStore | None = None,
) -> EntityRef:
    adapter = get_legal_entity_adapter(entity_type, store)
    entities = adapter.list_entities(company, user)
    entity = next((candidate for candidate in entities if candidate.entity_name == entity_name), None)
    if entity is None:
        raise ValueError(f"Legal entity {entity_type} {entity_name} is unavailable for company {company}")
    adapter.validate_entity(company, entity)
    return entity
