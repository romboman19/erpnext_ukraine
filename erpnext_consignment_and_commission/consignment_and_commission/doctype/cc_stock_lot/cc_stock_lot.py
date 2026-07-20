from decimal import Decimal

import frappe
from frappe.model.document import Document

from ...services.receipt import ReceiptValidationError, StockLotPolicy, validate_stock_lot

IMMUTABLE_FIELDS = (
    "source_method",
    "receipt",
    "receipt_item_row",
    "ownership_conversion",
    "own_receipt",
    "own_receipt_item_row",
    "purchase_invoice",
    "purchase_invoice_item",
    "partner_profile",
    "contract",
    "company",
    "location",
    "supplier",
    "relationship_model",
    "item_code",
    "stock_uom",
    "tracking_type",
    "batch_no",
    "serial_numbers",
    "received_qty",
    "received_datetime",
    "warehouse",
)


class CCStockLot(Document):
    def validate(self) -> None:
        if self.relationship_model == "OWN":
            if not self.own_receipt or not self.own_receipt_item_row:
                frappe.throw("OWN CC Stock Lot requires its CC Own Receipt Item Row")
            if self.receipt or self.receipt_item_row or self.partner_profile or self.contract:
                frappe.throw("OWN CC Stock Lot cannot carry third-party receipt ownership")
            if self.ownership_conversion and not frappe.db.exists(
                "CC Ownership Conversion", self.ownership_conversion
            ):
                frappe.throw("OWN CC Stock Lot conversion source does not exist")
        else:
            if not self.receipt or not self.receipt_item_row:
                frappe.throw("Third-party CC Stock Lot requires its CC Receipt Item Row")
            if not self.partner_profile or not self.contract:
                frappe.throw("Third-party CC Stock Lot requires Partner Profile and Contract")
            if self.own_receipt or self.own_receipt_item_row:
                frappe.throw("Third-party CC Stock Lot cannot carry an OWN receipt source")
        try:
            validate_stock_lot(
                StockLotPolicy(
                    relationship_model=self.relationship_model,
                    source_method=self.source_method,
                    received_qty=Decimal(str(self.received_qty or 0)),
                    reserved_qty=Decimal(str(self.reserved_qty or 0)),
                    lot_status=self.lot_status,
                )
            )
        except ReceiptValidationError as exc:
            frappe.throw(str(exc))

        if self.is_new():
            return
        persisted = frappe.db.get_value("CC Stock Lot", self.name, list(IMMUTABLE_FIELDS), as_dict=True)
        if not persisted:
            return
        changed = [
            fieldname
            for fieldname in IMMUTABLE_FIELDS
            if str(self.get(fieldname)) != str(persisted.get(fieldname))
        ]
        if changed:
            frappe.throw(f"CC Stock Lot ownership fields are immutable: {', '.join(changed)}")

    def on_trash(self) -> None:
        if (self.stock_entry or self.purchase_invoice) and not frappe.in_test:
            frappe.throw("Posted CC Stock Lot is immutable audit evidence and cannot be deleted")
