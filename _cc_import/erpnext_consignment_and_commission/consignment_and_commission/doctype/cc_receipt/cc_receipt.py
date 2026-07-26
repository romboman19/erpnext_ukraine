from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from ...integrations.stock_entry import cancel_receipt_stock, post_receipt_stock
from ...services.receipt import (
    ContractReceiptPolicy,
    ReceiptLinePolicy,
    ReceiptValidationError,
    receipt_warehouse,
    validate_contract_for_receipt,
    validate_receipt_line,
)
from ...services.tracking import (
    TRACKING_NONE,
    ReceiptTrackingPolicy,
    TrackingValidationError,
    validate_receipt_tracking,
)


class CCReceipt(Document):
    def validate(self) -> None:
        contract = frappe.db.get_value(
            "CC Contract",
            self.contract,
            [
                "status",
                "partner_profile",
                "supplier",
                "company",
                "location",
                "legal_entity_type",
                "legal_entity_name",
                "relationship_model",
                "currency",
                "valid_from",
                "valid_to",
            ],
            as_dict=True,
        )
        if not contract:
            frappe.throw("CC Receipt requires an existing CC Contract")
        try:
            validate_contract_for_receipt(
                ContractReceiptPolicy(
                    status=contract.status,
                    relationship_model=contract.relationship_model,
                    valid_from=getdate(contract.valid_from),
                    valid_to=getdate(contract.valid_to) if contract.valid_to else None,
                    posting_date=getdate(self.posting_date),
                )
            )
        except ReceiptValidationError as exc:
            frappe.throw(str(exc))

        self.partner_profile = contract.partner_profile
        self.supplier = contract.supplier
        self.company = contract.company
        self.location = contract.location
        self.legal_entity_type = contract.legal_entity_type
        self.legal_entity_name = contract.legal_entity_name
        self.relationship_model = contract.relationship_model
        self.currency = contract.currency

        location = frappe.db.get_value(
            "CC Location",
            self.location,
            ["company", "disabled", "commission_warehouse", "consignment_warehouse"],
            as_dict=True,
        )
        if not location or location.disabled or location.company != self.company:
            frappe.throw("CC Receipt requires an enabled Location for its Contract Company")
        try:
            self.warehouse = receipt_warehouse(
                self.relationship_model,
                commission_warehouse=location.commission_warehouse,
                consignment_warehouse=location.consignment_warehouse,
            )
        except ReceiptValidationError as exc:
            frappe.throw(str(exc))

        if not self.items:
            frappe.throw("CC Receipt requires at least one Item row")
        total = Decimal("0")
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
            except ReceiptValidationError as exc:
                frappe.throw(f"Row {row.idx}: {exc}")
            row.stock_qty = float(stock_qty)
            try:
                unit_value = Decimal(str(row.accounting_unit_value or 0))
            except (InvalidOperation, ValueError):
                frappe.throw(f"Row {row.idx}: 024 accounting unit value must be a number")
            if not unit_value.is_finite() or unit_value <= 0:
                frappe.throw(
                    f"Row {row.idx}: 024 accounting unit value must be positive and finite"
                )
            precision = int(row.precision("accounting_amount") or 2)
            quantum = Decimal("1").scaleb(-precision)
            row.accounting_amount = float(
                (stock_qty * unit_value).quantize(quantum, rounding=ROUND_HALF_UP)
            )
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
            total += stock_qty
        self.total_stock_qty = float(total)

    def before_submit(self) -> None:
        settings = frappe.get_single("CC Settings")
        if not settings.enabled:
            frappe.throw("CC Settings must be enabled before a CC Receipt can be submitted")
        enabled_field = {
            "COMMISSION": "enable_commission",
            "CONSIGNMENT": "enable_consignment",
        }[self.relationship_model]
        if not settings.get(enabled_field):
            frappe.throw(f"{self.relationship_model.title()} receipts are disabled in CC Settings")

    def on_submit(self) -> None:
        post_receipt_stock(self)
        from ...integrations.off_balance import post_receipt_off_balance

        post_receipt_off_balance(self)

    def before_cancel(self) -> None:
        from ...integrations.off_balance import cancel_reference_off_balance

        cancel_receipt_stock(self)
        cancel_reference_off_balance(self)

    def on_trash(self) -> None:
        if self.stock_entry and not frappe.in_test:
            frappe.throw("CC Receipt with a Stock Entry is immutable audit evidence and cannot be deleted")
