from decimal import Decimal

import frappe
from frappe.model.document import Document

from ...services.allocation import SOURCE_METHOD_RELATIONSHIP_MODEL
from ...services.reservation import (
    ReservationError,
    ReservationRequest,
    reservation_fingerprint,
    validate_allocation_transition,
)

WRITE_FLAG = "cc_allocation_service"
TEST_CLEANUP_FLAG = "cc_allocation_test_cleanup"
IMMUTABLE_FIELDS = (
    "idempotency_key",
    "request_fingerprint",
    "company",
    "location",
    "item_code",
    "requested_qty",
    "serial_no",
    "batch_no",
    "fiscal_policy",
    "allowed_warehouses",
    "reserved_at",
    "expires_at",
    "total_reserved_qty",
)


class CCAllocation(Document):
    def validate(self) -> None:
        if not getattr(frappe.flags, WRITE_FLAG, False):
            frappe.throw("CC Allocation is server-owned and cannot be edited directly")

        request = ReservationRequest(
            idempotency_key=self.idempotency_key,
            item_code=self.item_code,
            company=self.company,
            location=self.location,
            qty=Decimal(str(self.requested_qty or 0)),
            allowed_warehouses=frozenset(
                value for value in (self.allowed_warehouses or "").splitlines() if value
            ),
            serial_no=self.serial_no or None,
            batch_no=self.batch_no or None,
            fiscal_policy=self.fiscal_policy or None,
        )
        try:
            if reservation_fingerprint(request) != self.request_fingerprint:
                raise ReservationError("CC Allocation request fingerprint does not match its payload")
        except ReservationError as exc:
            frappe.throw(str(exc))

        if self.status == "RESERVED":
            total = sum((Decimal(str(row.qty or 0)) for row in self.slices), Decimal("0"))
            if not self.slices or total != Decimal(str(self.requested_qty)):
                frappe.throw("Reserved CC Allocation slices must equal the requested quantity")
            if total != Decimal(str(self.total_reserved_qty or 0)):
                frappe.throw("CC Allocation total reserved quantity is inconsistent")
            if not self.reserved_at or not self.expires_at:
                frappe.throw("Reserved CC Allocation requires reservation and expiry timestamps")
            for row in self.slices:
                expected = SOURCE_METHOD_RELATIONSHIP_MODEL.get(row.source_method)
                if expected != row.relationship_model:
                    frappe.throw(
                        f"Allocation slice {row.idx} source method requires relationship model {expected}"
                    )

        if self.is_new():
            return
        persisted = frappe.db.get_value(
            "CC Allocation",
            self.name,
            ["status", *IMMUTABLE_FIELDS],
            as_dict=True,
        )
        if not persisted:
            return
        try:
            validate_allocation_transition(persisted.status, self.status)
        except ReservationError as exc:
            frappe.throw(str(exc))
        changed = [
            fieldname
            for fieldname in IMMUTABLE_FIELDS
            if str(self.get(fieldname) or "") != str(persisted.get(fieldname) or "")
        ]
        if changed:
            frappe.throw(f"CC Allocation request fields are immutable: {', '.join(changed)}")

    def on_trash(self) -> None:
        if not frappe.in_test and not getattr(frappe.flags, TEST_CLEANUP_FLAG, False):
            frappe.throw("CC Allocation is immutable audit evidence and cannot be deleted")
