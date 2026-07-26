from decimal import Decimal, InvalidOperation

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime


class CCPriceVersion(Document):
    def validate(self) -> None:
        lot = frappe.db.get_value(
            "CC Stock Lot",
            self.stock_lot,
            [
                "lot_status",
                "company",
                "location",
                "item_code",
                "contract",
                "supplier",
                "relationship_model",
                "received_datetime",
            ],
            as_dict=True,
        )
        if not lot or lot.lot_status not in {"OPEN", "BLOCKED"}:
            frappe.throw("CC Price Version requires an open or blocked CC Stock Lot")
        if lot.relationship_model != "CONSIGNMENT" or not lot.contract:
            frappe.throw("CC Price Version is required only for a consignment Contract lot")
        contract = frappe.db.get_value(
            "CC Contract",
            lot.contract,
            ["status", "currency", "relationship_model"],
            as_dict=True,
        )
        if not contract or contract.relationship_model != "CONSIGNMENT":
            frappe.throw("CC Price Version lot has no valid consignment Contract")
        if contract.status != "ACTIVE" and self.docstatus < 2:
            frappe.throw("New CC Price Version requires an active Contract")

        self.company = lot.company
        self.location = lot.location
        self.item_code = lot.item_code
        self.contract = lot.contract
        self.supplier = lot.supplier
        self.currency = contract.currency
        try:
            partner_rate = Decimal(str(self.partner_rate))
        except (InvalidOperation, ValueError):
            frappe.throw("Partner Unit Rate must be a decimal number")
        if not partner_rate.is_finite() or partner_rate <= 0:
            frappe.throw("Partner Unit Rate must be finite and greater than zero")
        if not self.valid_from:
            frappe.throw("CC Price Version requires Valid From")
        if get_datetime(self.valid_from) < get_datetime(lot.received_datetime):
            frappe.throw("CC Price Version cannot start before the lot receipt datetime")
        if self.valid_to and get_datetime(self.valid_to) <= get_datetime(self.valid_from):
            frappe.throw("CC Price Version Valid To must be after Valid From")

        duplicate = frappe.db.get_value(
            "CC Price Version",
            {
                "stock_lot": self.stock_lot,
                "valid_from": self.valid_from,
                "name": ("!=", self.name or ""),
                "docstatus": ("<", 2),
            },
            "name",
        )
        if duplicate:
            frappe.throw(f"CC Price Version {duplicate} already starts at this datetime")

    def before_submit(self) -> None:
        settings = frappe.get_single("CC Settings")
        if not settings.enabled or not settings.enable_consignment:
            frappe.throw("Consignment must be enabled before a CC Price Version can be submitted")
        frappe.db.sql(
            "select name from `tabCC Stock Lot` where name = %s for update",
            (self.stock_lot,),
        )
        future = frappe.db.get_value(
            "CC Price Version",
            {
                "stock_lot": self.stock_lot,
                "docstatus": 1,
                "valid_from": (">=", self.valid_from),
                "name": ("!=", self.name),
            },
            "name",
        )
        if future:
            frappe.throw(
                f"CC Price Version {future} already covers this or a later datetime; "
                "use a correction workflow for backdated changes"
            )
        previous_name = frappe.db.get_value(
            "CC Price Version",
            {
                "stock_lot": self.stock_lot,
                "docstatus": 1,
                "valid_from": ("<", self.valid_from),
            },
            "name",
            order_by="valid_from desc",
        )
        if previous_name:
            frappe.db.set_value(
                "CC Price Version",
                previous_name,
                {"status": "SUPERSEDED", "valid_to": self.valid_from},
                update_modified=False,
            )
            self.supersedes = previous_name
        self.status = "ACTIVE"
        self.approved_by = frappe.session.user
        self.approved_at = now_datetime()

    def before_cancel(self) -> None:
        if self.status != "ACTIVE":
            frappe.throw("Only the latest active CC Price Version can be cancelled")
        if frappe.db.exists("DocType", "CC Sale Allocation") and frappe.db.exists(
            "CC Sale Allocation",
            {"price_version": self.name, "status": ("!=", "CANCELLED")},
        ):
            frappe.throw("CC Price Version used by a sale cannot be cancelled")
        frappe.db.sql(
            "select name from `tabCC Stock Lot` where name = %s for update",
            (self.stock_lot,),
        )
        if self.supersedes:
            frappe.db.set_value(
                "CC Price Version",
                self.supersedes,
                {"status": "ACTIVE", "valid_to": None},
                update_modified=False,
            )
        self.status = "CANCELLED"

    def on_trash(self) -> None:
        if self.docstatus != 0 and not frappe.in_test:
            frappe.throw("Submitted CC Price Version is immutable audit evidence")
