import frappe
from frappe.model.document import Document

from ...adapters.legal_entity import resolve_legal_entity
from ...services.foundation import FoundationValidationError, LocationPolicy, validate_location_policy


class CCLocation(Document):
    def validate(self) -> None:
        try:
            validate_location_policy(
                LocationPolicy(
                    company=self.company,
                    legal_entity_type=self.legal_entity_type,
                    legal_entity_name=self.legal_entity_name,
                    own_warehouse=self.own_warehouse,
                    commission_warehouse=self.commission_warehouse,
                    consignment_warehouse=self.consignment_warehouse,
                )
            )
            entity = resolve_legal_entity(
                self.company,
                self.legal_entity_type,
                self.legal_entity_name,
                frappe.session.user,
            )
        except (FoundationValidationError, ValueError) as exc:
            frappe.throw(str(exc))

        self.legal_entity_label = entity.display_name
        for fieldname in ("own_warehouse", "commission_warehouse", "consignment_warehouse"):
            self._validate_warehouse(fieldname)
        self._validate_pos_cash_desk()
        self._validate_gsf_provider()

    def _validate_warehouse(self, fieldname: str) -> None:
        warehouse_name = self.get(fieldname)
        warehouse = frappe.db.get_value(
            "Warehouse",
            warehouse_name,
            ["company", "is_group", "disabled"],
            as_dict=True,
        )
        if not warehouse:
            frappe.throw(f"Warehouse {warehouse_name} does not exist")
        if warehouse.company != self.company:
            frappe.throw(f"Warehouse {warehouse_name} must belong to company {self.company}")
        if warehouse.is_group:
            frappe.throw(f"Warehouse {warehouse_name} must be a leaf warehouse")
        if warehouse.disabled:
            frappe.throw(f"Warehouse {warehouse_name} is disabled")

    def _validate_gsf_provider(self) -> None:
        if not self.gsf_physical_location:
            if self.read_stock_enabled:
                frappe.throw("Select a GSF Physical Location before enabling the provider")
            return
        location = frappe.db.get_value(
            "GSF Physical Location",
            self.gsf_physical_location,
            ["company_group", "disabled"],
            as_dict=True,
        )
        if not location or location.disabled:
            frappe.throw("The linked GSF Physical Location must be active")
        if not frappe.db.exists(
            "GSF Group Member",
            {
                "parent": location.company_group,
                "company": self.company,
                "enabled": 1,
                "can_sell_stock": 1,
            },
        ):
            frappe.throw(
                f"Company {self.company} must be an active selling member of "
                f"GSF group {location.company_group}"
            )

    def _validate_pos_cash_desk(self) -> None:
        if not self.pos_cash_desk:
            return
        desk = frappe.db.get_value(
            "POS Cash Desk",
            self.pos_cash_desk,
            ["company", "status"],
            as_dict=True,
        )
        if not desk or desk.status != "Active" or desk.company != self.company:
            frappe.throw("POS Cash Desk must be active and belong to the CC Location Company")
