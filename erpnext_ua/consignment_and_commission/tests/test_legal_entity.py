from unittest import TestCase

from erpnext_ua.consignment_and_commission.adapters.legal_entity import (
    CompanyLegalEntityAdapter,
    EntityRef,
    FOPProfileRecord,
    get_legal_entity_adapter,
    resolve_legal_entity,
)


class FakeLegalEntityStore:
    def __init__(self, installed: bool = True) -> None:
        self.installed = installed

    def has_doctype(self, doctype: str) -> bool:
        return doctype == "FOP Profile" and self.installed

    def list_fop_profiles(self, company: str, user: str) -> list[FOPProfileRecord]:
        del user
        return [FOPProfileRecord("FOP-1", company, "Test FOP", "Active")]

    def get_fop_profile(self, name: str, user: str) -> FOPProfileRecord | None:
        del user
        return FOPProfileRecord(name, "Company", "Test FOP", "Active")


class LegalEntityAdapterTests(TestCase):
    def test_company_adapter_rejects_cross_company_reference(self) -> None:
        adapter = CompanyLegalEntityAdapter()
        with self.assertRaisesRegex(ValueError, "must match"):
            adapter.validate_entity("Company A", EntityRef("Company", "Company B", "Company B", "Company B"))

    def test_fop_profile_is_resolved_through_store_contract(self) -> None:
        entity = resolve_legal_entity("Company", "FOP Profile", "FOP-1", "user@example.com", FakeLegalEntityStore())
        self.assertEqual(entity.display_name, "Test FOP")
        self.assertEqual(entity.company, "Company")

    def test_missing_optional_doctype_is_explicit(self) -> None:
        with self.assertRaisesRegex(ValueError, "not installed"):
            get_legal_entity_adapter("FOP Profile", FakeLegalEntityStore(installed=False))

    def test_unknown_legal_entity_type_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            get_legal_entity_adapter("Custom Entity", FakeLegalEntityStore())
