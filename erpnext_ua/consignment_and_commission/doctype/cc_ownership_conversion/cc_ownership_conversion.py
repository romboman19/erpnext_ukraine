from __future__ import annotations

from decimal import Decimal, InvalidOperation

import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from ...services.own_receipt import (
    OwnReceiptPolicy,
    OwnReceiptValidationError,
    validate_own_receipt,
)
from ...services.ownership_conversion import (
    OwnershipConversionError,
    OwnershipConversionRequest,
    ownership_conversion_fingerprint,
)
from ...setup.ownership_dimension import OWNERSHIP_FIELD

IMMUTABLE_FIELDS = (
    "idempotency_key",
    "request_fingerprint",
    "posting_date",
    "posting_time",
    "source_lot",
    "reason",
    "company",
    "location",
    "partner_profile",
    "supplier",
    "contract",
    "relationship_model",
    "source_method",
    "item_code",
    "source_warehouse",
    "stock_uom",
    "tracking_type",
    "source_batch_no",
    "qty",
    "serial_numbers",
    "target_batch_no",
    "unit_cost",
    "currency",
    "exchange_rate",
    "due_date",
    "supplier_invoice_no",
    "supplier_invoice_date",
)


def _serials(value: str | None) -> tuple[str, ...]:
    return tuple(line.strip() for line in (value or "").splitlines() if line.strip())


class CCOwnershipConversion(Document):
    def validate(self) -> None:
        lot = frappe.db.get_value(
            "CC Stock Lot",
            self.source_lot,
            [
                "lot_status",
                "company",
                "location",
                "partner_profile",
                "supplier",
                "contract",
                "relationship_model",
                "item_code",
                "warehouse",
                "stock_uom",
                "tracking_type",
                "batch_no",
            ],
            as_dict=True,
        )
        if not lot or lot.relationship_model not in {"COMMISSION", "CONSIGNMENT"}:
            frappe.throw("CC Ownership Conversion requires commission or consignment stock")
        if lot.lot_status not in {"OPEN", "BLOCKED", "EXHAUSTED"}:
            frappe.throw(f"CC Stock Lot {self.source_lot} cannot be converted from {lot.lot_status}")
        mapped_fields = {
            "company": "company",
            "location": "location",
            "partner_profile": "partner_profile",
            "supplier": "supplier",
            "contract": "contract",
            "relationship_model": "relationship_model",
            "item_code": "item_code",
            "source_warehouse": "warehouse",
            "stock_uom": "stock_uom",
            "tracking_type": "tracking_type",
            "source_batch_no": "batch_no",
        }
        for target, source in mapped_fields.items():
            self.set(target, lot.get(source))

        try:
            qty = Decimal(str(self.qty or 0))
            unit_cost = Decimal(str(self.unit_cost or 0))
            exchange_rate = Decimal(str(self.exchange_rate or 0))
        except (InvalidOperation, ValueError):
            frappe.throw("Conversion quantity, cost and exchange rate must be numbers")
        serials = _serials(self.serial_numbers)
        if len(set(serials)) != len(serials):
            frappe.throw("Conversion Serial Nos must be unique")
        posted = bool(self.own_receipt or self.purchase_invoice)
        if self.tracking_type == "SERIAL":
            if qty != qty.to_integral_value() or Decimal(len(serials)) != qty:
                frappe.throw("Serialized conversion requires one exact Serial No per unit")
            for serial_no in serials:
                serial = frappe.db.get_value(
                    "Serial No",
                    serial_no,
                    ["item_code", "warehouse", OWNERSHIP_FIELD],
                    as_dict=True,
                )
                expected_owner = self.target_lot if posted else self.source_lot
                expected_warehouse = (
                    frappe.db.get_value("CC Stock Lot", self.target_lot, "warehouse")
                    if posted and self.target_lot
                    else self.source_warehouse
                )
                if (
                    not serial
                    or serial.item_code != self.item_code
                    or serial.warehouse != expected_warehouse
                    or serial.get(OWNERSHIP_FIELD) != expected_owner
                ):
                    frappe.throw(
                        f"Serial No {serial_no} is not active in the expected conversion lot"
                    )
            self.serial_numbers = "\n".join(serials)
            if self.target_batch_no:
                frappe.throw("Serialized conversion cannot set a target Batch")
        elif serials:
            frappe.throw("Serial Numbers are allowed only for serialized stock")

        if self.tracking_type == "BATCH":
            if (
                not self.source_batch_no
                or frappe.db.get_value("Batch", self.source_batch_no, OWNERSHIP_FIELD)
                != self.source_lot
            ):
                frappe.throw("Conversion source Batch must belong to its source CC Stock Lot")
            if not self.target_batch_no or self.target_batch_no == self.source_batch_no:
                frappe.throw("Batch conversion requires a distinct target OWN Batch")
            if frappe.db.exists("Batch", self.target_batch_no):
                batch = frappe.db.get_value(
                    "Batch",
                    self.target_batch_no,
                    ["item", OWNERSHIP_FIELD],
                    as_dict=True,
                )
                if not posted or batch.item != self.item_code or batch.get(OWNERSHIP_FIELD) != self.target_lot:
                    frappe.throw(f"Target Batch {self.target_batch_no} is already in use")
        elif self.target_batch_no:
            frappe.throw("Target Batch is allowed only for batch-tracked stock")

        if not (self.reason or "").strip():
            frappe.throw("Ownership conversion reason is required")
        self.reason = self.reason.strip()
        company_currency = frappe.get_cached_value("Company", self.company, "default_currency")
        try:
            self.due_date = validate_own_receipt(
                OwnReceiptPolicy(
                    source_method=self.source_method,
                    posting_date=getdate(self.posting_date),
                    due_date=getdate(self.due_date) if self.due_date else None,
                    currency=self.currency,
                    company_currency=company_currency,
                    conversion_rate=exchange_rate,
                )
            )
        except OwnReceiptValidationError as exc:
            frappe.throw(str(exc))
        if not unit_cost.is_finite() or unit_cost <= 0:
            frappe.throw("Conversion unit cost must be positive and finite")

        if self.idempotency_key:
            try:
                expected = ownership_conversion_fingerprint(self.as_request(qty, unit_cost, exchange_rate, serials))
            except OwnershipConversionError as exc:
                frappe.throw(str(exc))
            if self.request_fingerprint and self.request_fingerprint != expected:
                frappe.throw("CC Ownership Conversion fingerprint does not match its payload")
            self.request_fingerprint = expected
        elif self.request_fingerprint:
            frappe.throw("Conversion fingerprint requires an idempotency key")

        if self.is_new():
            self.previous_lot_status = lot.lot_status
            return
        persisted = frappe.db.get_value(
            "CC Ownership Conversion",
            self.name,
            [*IMMUTABLE_FIELDS, "source_issue", "own_receipt"],
            as_dict=True,
        )
        if persisted and (persisted.source_issue or persisted.own_receipt):
            changed = [
                fieldname
                for fieldname in IMMUTABLE_FIELDS
                if str(self.get(fieldname) or "") != str(persisted.get(fieldname) or "")
            ]
            if changed:
                frappe.throw(f"Posted CC Ownership Conversion is immutable: {', '.join(changed)}")

    def as_request(
        self,
        qty: Decimal | None = None,
        unit_cost: Decimal | None = None,
        exchange_rate: Decimal | None = None,
        serials: tuple[str, ...] | None = None,
    ) -> OwnershipConversionRequest:
        return OwnershipConversionRequest(
            idempotency_key=self.idempotency_key,
            posting_date=getdate(self.posting_date),
            posting_time=self.posting_time,
            source_lot=self.source_lot,
            qty=qty if qty is not None else Decimal(str(self.qty)),
            source_method=self.source_method,
            unit_cost=unit_cost if unit_cost is not None else Decimal(str(self.unit_cost)),
            currency=self.currency,
            exchange_rate=(
                exchange_rate if exchange_rate is not None else Decimal(str(self.exchange_rate))
            ),
            reason=self.reason,
            due_date=(
                getdate(self.due_date)
                if self.source_method == "DEFERRED_PURCHASE" and self.due_date
                else None
            ),
            supplier_invoice_no=self.supplier_invoice_no or "",
            supplier_invoice_date=(
                getdate(self.supplier_invoice_date) if self.supplier_invoice_date else None
            ),
            serial_numbers=serials if serials is not None else _serials(self.serial_numbers),
            target_batch_no=self.target_batch_no or "",
        )

    def before_submit(self) -> None:
        from ...integrations.ownership_conversions import validate_conversion_availability

        settings = frappe.get_single("CC Settings")
        fields = {
            "COMMISSION": "enable_commission",
            "CONSIGNMENT": "enable_consignment",
            "BUYOUT": "enable_buyout",
            "DEFERRED_PURCHASE": "enable_deferred_purchase",
        }
        if not settings.enabled or not all(
            settings.get(fields[value]) for value in (self.relationship_model, self.source_method)
        ):
            frappe.throw("Source relationship or target purchase method is disabled in CC Settings")
        validate_conversion_availability(self)

    def on_submit(self) -> None:
        from ...integrations.off_balance import post_partner_exit_off_balance
        from ...integrations.ownership_conversions import post_ownership_conversion

        post_ownership_conversion(self)
        post_partner_exit_off_balance(self)

    def before_cancel(self) -> None:
        from ...integrations.off_balance import cancel_reference_off_balance
        from ...integrations.ownership_conversions import cancel_ownership_conversion

        cancel_reference_off_balance(self)
        cancel_ownership_conversion(self)

    def on_cancel(self) -> None:
        self.db_set("status", "CANCELLED", update_modified=False)
        self.status = "CANCELLED"

    def on_trash(self) -> None:
        if (self.source_issue or self.own_receipt) and not frappe.in_test:
            frappe.throw("Posted CC Ownership Conversion is immutable audit evidence")
