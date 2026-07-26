from decimal import Decimal

import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from ...services.foundation import (
    ContractPolicy,
    FoundationValidationError,
    date_ranges_overlap,
    partner_allows_relationship,
    validate_contract_policy,
)


class CCContract(Document):
    def validate(self) -> None:
        partner = frappe.db.get_value(
            "CC Partner Profile",
            self.partner_profile,
            ["supplier", "disabled", "allowed_relationship_models"],
            as_dict=True,
        )
        if not partner or partner.disabled:
            frappe.throw("CC Partner Profile must exist and be enabled")

        location = frappe.db.get_value(
            "CC Location",
            self.location,
            ["company", "disabled", "legal_entity_type", "legal_entity_name"],
            as_dict=True,
        )
        if not location or location.disabled:
            frappe.throw("CC Location must exist and be enabled")
        if location.company != self.company:
            frappe.throw("CC Contract company must match its CC Location company")

        self.supplier = partner.supplier
        self.legal_entity_type = location.legal_entity_type
        self.legal_entity_name = location.legal_entity_name

        try:
            if not partner_allows_relationship(partner.allowed_relationship_models, self.relationship_model):
                raise FoundationValidationError("Partner Profile does not allow this relationship model")
            validate_contract_policy(
                ContractPolicy(
                    relationship_model=self.relationship_model,
                    status=self.status,
                    valid_from=getdate(self.valid_from),
                    valid_to=getdate(self.valid_to) if self.valid_to else None,
                    commission_rate=Decimal(str(self.commission_rate or 0)),
                    settlement_deadline_days=int(self.settlement_deadline_days or 0),
                    fiscal_policy=self.fiscal_policy,
                    price_authority=self.price_authority,
                )
            )
        except FoundationValidationError as exc:
            frappe.throw(str(exc))

        if self.status == "ACTIVE":
            self._validate_no_active_overlap()

    def _validate_no_active_overlap(self) -> None:
        filters = {
            "partner_profile": self.partner_profile,
            "company": self.company,
            "location": self.location,
            "relationship_model": self.relationship_model,
            "status": "ACTIVE",
        }
        for existing in frappe.get_all(
            "CC Contract",
            filters=filters,
            fields=["name", "valid_from", "valid_to"],
        ):
            if existing.name == self.name:
                continue
            if date_ranges_overlap(
                getdate(self.valid_from),
                getdate(self.valid_to) if self.valid_to else None,
                getdate(existing.valid_from),
                getdate(existing.valid_to) if existing.valid_to else None,
            ):
                frappe.throw(f"Active contract period overlaps CC Contract {existing.name}")
