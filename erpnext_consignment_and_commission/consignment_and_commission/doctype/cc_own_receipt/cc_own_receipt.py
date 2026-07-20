from decimal import Decimal

import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from ...integrations.purchase_invoice import cancel_own_receipt_purchase, post_own_receipt_purchase
from ...services.own_receipt import (
    OwnReceiptLinePolicy,
    OwnReceiptPolicy,
    OwnReceiptValidationError,
    own_receipt_line_amount,
    validate_own_receipt,
)
from ...services.receipt import (
    ReceiptLinePolicy,
    ReceiptValidationError,
    validate_receipt_line,
)
from ...services.tracking import (
    TRACKING_NONE,
    ReceiptTrackingPolicy,
    TrackingValidationError,
    validate_receipt_tracking,
)


class CCOwnReceipt(Document):
    def validate(self) -> None:
        company = frappe.db.get_value(
            "Company",
            self.company,
            ["default_currency"],
            as_dict=True,
        )
        if not company:
            frappe.throw("CC Own Receipt requires an existing Company")
        supplier = frappe.db.get_value("Supplier", self.supplier, ["disabled"], as_dict=True)
        if not supplier or supplier.disabled:
            frappe.throw("CC Own Receipt requires an enabled Supplier")
        location = frappe.db.get_value(
            "CC Location",
            self.location,
            ["company", "disabled", "own_warehouse"],
            as_dict=True,
        )
        if not location or location.disabled or location.company != self.company:
            frappe.throw("CC Own Receipt requires an enabled Location for its Company")
        self.warehouse = location.own_warehouse

        try:
            self.due_date = validate_own_receipt(
                OwnReceiptPolicy(
                    source_method=self.source_method,
                    posting_date=getdate(self.posting_date),
                    due_date=getdate(self.due_date) if self.due_date else None,
                    currency=self.currency,
                    company_currency=company.default_currency,
                    conversion_rate=Decimal(str(self.conversion_rate or 0)),
                )
            )
        except OwnReceiptValidationError as exc:
            frappe.throw(str(exc))

        if not self.items:
            frappe.throw("CC Own Receipt requires at least one Item row")
        total_qty = Decimal("0")
        total_amount = Decimal("0")
        for row in self.items:
            item = frappe.db.get_value(
                "Item",
                row.item_code,
                [
                    "item_name",
                    "description",
                    "stock_uom",
                    "disabled",
                    "is_stock_item",
                    "has_serial_no",
                    "has_batch_no",
                    "create_new_batch",
                    "batch_number_series",
                    "serial_no_series",
                ],
                as_dict=True,
            )
            if not item:
                frappe.throw(f"Item {row.item_code!r} does not exist")
            row.item_name = item.item_name
            row.description = row.description or item.description or item.item_name
            row.stock_uom = item.stock_uom
            row.uom = row.uom or item.stock_uom
            row.conversion_factor = row.conversion_factor or 1
            try:
                stock_qty = validate_receipt_line(
                    ReceiptLinePolicy(
                        item_code=row.item_code,
                        qty=Decimal(str(row.qty or 0)),
                        uom=row.uom,
                        stock_uom=item.stock_uom,
                        conversion_factor=Decimal(str(row.conversion_factor or 0)),
                        is_stock_item=bool(item.is_stock_item),
                        disabled=bool(item.disabled),
                        has_serial_no=bool(item.has_serial_no),
                        has_batch_no=bool(item.has_batch_no),
                    )
                )
                amount = own_receipt_line_amount(
                    OwnReceiptLinePolicy(stock_qty, Decimal(str(row.rate or 0)))
                )
            except (ReceiptValidationError, OwnReceiptValidationError) as exc:
                frappe.throw(f"Row {row.idx}: {exc}")
            row.stock_qty = float(stock_qty)
            row.amount = float(amount)
            try:
                tracking = validate_receipt_tracking(
                    ReceiptTrackingPolicy(
                        stock_qty=stock_qty,
                        has_batch_no=bool(item.has_batch_no),
                        has_serial_no=bool(item.has_serial_no),
                        batch_no=row.batch_no,
                        serial_numbers=row.serial_numbers,
                        create_new_batch=bool(item.create_new_batch),
                        batch_number_series=item.batch_number_series,
                        serial_no_series=item.serial_no_series,
                    )
                )
            except TrackingValidationError as exc:
                frappe.throw(f"Row {row.idx}: {exc}")

            if row.batch_no:
                batch_item = frappe.db.get_value("Batch", row.batch_no, "item")
                if not batch_item:
                    frappe.throw(f"Row {row.idx}: Batch {row.batch_no!r} does not exist")
                if batch_item != row.item_code:
                    frappe.throw(
                        f"Row {row.idx}: Batch {row.batch_no!r} belongs to Item {batch_item!r}"
                    )
            for serial_no in tracking.serial_numbers:
                if frappe.db.exists("Serial No", serial_no):
                    from ...integrations.ownership_conversions import (
                        conversion_allows_existing_serial,
                    )

                    if not conversion_allows_existing_serial(self, serial_no):
                        frappe.throw(f"Row {row.idx}: Serial No {serial_no!r} already exists")

            row.tracking_type = tracking.tracking_type
            row.serial_numbers = "\n".join(tracking.serial_numbers) or None
            if tracking.tracking_type != TRACKING_NONE and not frappe.db.get_single_value(
                "Stock Settings", "enable_serial_and_batch_no_for_item"
            ):
                frappe.throw(
                    f"Row {row.idx}: enable 'Activate Serial and Batch No for Item' "
                    "in Stock Settings before receiving tracked stock"
                )
            total_qty += stock_qty
            total_amount += amount
        self.total_stock_qty = float(total_qty)
        self.total_amount = float(total_amount)
        if self.ownership_conversion:
            from ...integrations.ownership_conversions import validate_conversion_own_receipt

            validate_conversion_own_receipt(self)

    def before_submit(self) -> None:
        settings = frappe.get_single("CC Settings")
        if not settings.enabled:
            frappe.throw("CC Settings must be enabled before a CC Own Receipt can be submitted")
        enabled_field = {
            "BUYOUT": "enable_buyout",
            "DEFERRED_PURCHASE": "enable_deferred_purchase",
        }[self.source_method]
        if not settings.get(enabled_field):
            frappe.throw(f"{self.source_method.replace('_', ' ').title()} is disabled in CC Settings")

    def on_submit(self) -> None:
        post_own_receipt_purchase(self)

    def before_cancel(self) -> None:
        if self.ownership_conversion:
            from ...integrations.ownership_conversions import guard_conversion_own_receipt

            guard_conversion_own_receipt(self)
        cancel_own_receipt_purchase(self)

    def on_trash(self) -> None:
        if self.purchase_invoice and not frappe.in_test:
            frappe.throw(
                "CC Own Receipt with a Purchase Invoice is immutable audit evidence and cannot be deleted"
            )
