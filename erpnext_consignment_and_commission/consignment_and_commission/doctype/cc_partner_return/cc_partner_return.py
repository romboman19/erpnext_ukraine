from __future__ import annotations

from decimal import Decimal, InvalidOperation

import frappe
from frappe.model.document import Document

from ...setup.ownership_dimension import OWNERSHIP_FIELD

IMMUTABLE_FIELDS = (
    "idempotency_key",
    "request_fingerprint",
    "posting_date",
    "posting_time",
    "source_lot",
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
    "qty",
    "serial_numbers",
    "reason",
)


def _serials(value: str | None) -> tuple[str, ...]:
    return tuple(line.strip() for line in (value or "").splitlines() if line.strip())


class CCPartnerReturn(Document):
    def validate(self) -> None:
        from ...services.partner_return import (
            PartnerReturnRequest,
            partner_return_fingerprint,
        )

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
            frappe.throw("CC Partner Return requires a commission or consignment Stock Lot")
        if lot.lot_status not in {"OPEN", "BLOCKED", "EXHAUSTED"}:
            frappe.throw(f"CC Stock Lot {self.source_lot} cannot be returned from {lot.lot_status}")
        for fieldname in (
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
        ):
            self.set(fieldname, lot.get(fieldname))
        try:
            qty = Decimal(str(self.qty or 0))
        except (InvalidOperation, ValueError):
            frappe.throw("Partner return quantity must be a number")
        if not qty.is_finite() or qty <= 0:
            frappe.throw("Partner return quantity must be positive and finite")
        serials = _serials(self.serial_numbers)
        if len(set(serials)) != len(serials):
            frappe.throw("Partner return Serial Nos must be unique")
        if self.tracking_type == "SERIAL":
            if qty != qty.to_integral_value() or Decimal(len(serials)) != qty:
                frappe.throw("Serialized partner return requires one exact Serial No per unit")
            for serial_no in serials:
                serial = frappe.db.get_value(
                    "Serial No",
                    serial_no,
                    ["item_code", "warehouse", OWNERSHIP_FIELD],
                    as_dict=True,
                )
                if (
                    not serial
                    or serial.item_code != self.item_code
                    or serial.warehouse != self.warehouse
                    or serial.get(OWNERSHIP_FIELD) != self.source_lot
                ):
                    frappe.throw(
                        f"Serial No {serial_no} is not active in CC Stock Lot {self.source_lot}"
                    )
            self.serial_numbers = "\n".join(serials)
        elif serials:
            frappe.throw("Serial Numbers are allowed only for serialized stock")
        if self.tracking_type == "BATCH":
            owner = frappe.db.get_value("Batch", self.batch_no, OWNERSHIP_FIELD)
            if not self.batch_no or owner != self.source_lot:
                frappe.throw("Partner return Batch must belong to its source CC Stock Lot")
        if not (self.reason or "").strip():
            frappe.throw("Partner return reason is required")
        self.reason = self.reason.strip()
        if self.idempotency_key:
            request = PartnerReturnRequest(
                idempotency_key=self.idempotency_key,
                posting_date=self.posting_date,
                posting_time=self.posting_time,
                source_lot=self.source_lot,
                qty=qty,
                reason=self.reason,
                serial_numbers=serials,
            )
            expected_fingerprint = partner_return_fingerprint(request)
            if self.request_fingerprint and self.request_fingerprint != expected_fingerprint:
                frappe.throw("CC Partner Return request fingerprint does not match its payload")
            self.request_fingerprint = expected_fingerprint
        elif self.request_fingerprint:
            frappe.throw("CC Partner Return request fingerprint requires an idempotency key")
        if self.is_new():
            self.previous_lot_status = lot.lot_status
            return
        persisted = frappe.db.get_value(
            "CC Partner Return",
            self.name,
            [*IMMUTABLE_FIELDS, "stock_entry"],
            as_dict=True,
        )
        if persisted and persisted.stock_entry:
            changed = [
                fieldname
                for fieldname in IMMUTABLE_FIELDS
                if str(self.get(fieldname) or "") != str(persisted.get(fieldname) or "")
            ]
            if changed:
                frappe.throw(f"Posted CC Partner Return is immutable: {', '.join(changed)}")

    def before_submit(self) -> None:
        from ...integrations.partner_returns import validate_partner_return_availability

        settings = frappe.get_single("CC Settings")
        enabled_field = {
            "COMMISSION": "enable_commission",
            "CONSIGNMENT": "enable_consignment",
        }[self.relationship_model]
        if not settings.enabled or not settings.get(enabled_field):
            frappe.throw(f"{self.relationship_model.title()} is disabled in CC Settings")
        validate_partner_return_availability(self)

    def on_submit(self) -> None:
        from ...integrations.off_balance import post_partner_exit_off_balance
        from ...integrations.partner_returns import post_partner_return

        post_partner_return(self)
        post_partner_exit_off_balance(self)

    def before_cancel(self) -> None:
        from ...integrations.off_balance import cancel_reference_off_balance
        from ...integrations.partner_returns import cancel_partner_return

        cancel_reference_off_balance(self)
        cancel_partner_return(self)

    def on_cancel(self) -> None:
        self.db_set("status", "CANCELLED", update_modified=False)
        self.status = "CANCELLED"

    def on_trash(self) -> None:
        if self.stock_entry and not frappe.in_test:
            frappe.throw("Posted CC Partner Return is immutable audit evidence")
