"""Controller for GSF Allocation (§9.12).

Server-owned: the reservation service is the only writer, because every
invariant here — the fingerprint matching the payload, the slices summing to
the request, the lifecycle — is only meaningful if no one can hand-edit a row
between two service calls.
"""

from __future__ import annotations

from decimal import Decimal

import frappe
from frappe.model.document import Document

from erpnext_ua.group_stock_fifo.services.domain import GSFError
from erpnext_ua.group_stock_fifo.services.reservation import (
    ALLOCATION_RESERVED,
    ReservationRequest,
    reservation_fingerprint,
    validate_allocation_transition,
)

WRITE_FLAG = "gsf_allocation_service"

#: §34.3 — once the request is recorded, correcting it means a new allocation,
#: not an edited one.
IMMUTABLE_FIELDS = (
    "idempotency_key",
    "request_fingerprint",
    "company_group",
    "physical_location",
    "seller_company",
    "item_code",
    "requested_qty",
    "allowed_warehouses",
    "serial_no",
    "batch_no",
    "item_policy_snapshot",
    "external_row_id",
    "posting_date",
    "checkout",
    "reserved_at",
    "expires_at",
)


def _comparable(value: object) -> str:
    """Compare a stored value against an in-memory one without false positives.

    The saved row hands back a `date`, a `float` or `None` where the edited
    document may still hold the string the caller passed in, and a bare `str()`
    on both would report every save as tampering.
    """
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float, Decimal)):
        return str(Decimal(str(value)).normalize())
    return str(value).strip()


class GSFAllocation(Document):
    def validate(self) -> None:
        if not getattr(frappe.flags, WRITE_FLAG, False):
            frappe.throw(
                "GSF Allocation is written by the reservation service and cannot be edited directly",
                title="ALLOCATION_CONFLICT",
            )
        try:
            self._check_fingerprint()
            if self.status == ALLOCATION_RESERVED:
                self._check_reserved_slices()
            self._check_transition()
        except GSFError as error:
            frappe.throw(str(error), title=error.code)

    def _check_fingerprint(self) -> None:
        request = ReservationRequest(
            idempotency_key=self.idempotency_key,
            company_group=self.company_group,
            physical_location=self.physical_location,
            seller_company=self.seller_company,
            item_code=self.item_code,
            qty=Decimal(str(self.requested_qty or 0)),
            allowed_warehouses=self.warehouse_set(),
            serial_no=self.serial_no or None,
            batch_no=self.batch_no or None,
            item_policy=self.item_policy_snapshot or None,
            external_row_id=self.external_row_id or None,
            posting_date=str(self.posting_date) if self.posting_date else None,
            checkout=self.checkout or None,
        )
        if reservation_fingerprint(request) != self.request_fingerprint:
            raise GSFError(
                "GSF Allocation fingerprint does not match its own payload",
                "IDEMPOTENCY_CONFLICT",
            )

    def _check_reserved_slices(self) -> None:
        """A reservation that does not add up is worse than one that failed."""
        total = sum((Decimal(str(row.qty or 0)) for row in self.slices), Decimal("0"))
        if not self.slices or total != Decimal(str(self.requested_qty)):
            raise GSFError(
                f"Reserved slices total {total}, requested {self.requested_qty}",
                "ALLOCATION_CONFLICT",
            )
        if total != Decimal(str(self.allocated_qty or 0)):
            raise GSFError("Allocated quantity disagrees with the slices", "ALLOCATION_CONFLICT")
        if not self.reserved_at or not self.expires_at:
            raise GSFError("A reserved allocation needs its TTL window", "ALLOCATION_EXPIRED")

    def _check_transition(self) -> None:
        if self.is_new():
            return
        persisted = frappe.db.get_value(
            "GSF Allocation", self.name, ["status", *IMMUTABLE_FIELDS], as_dict=True
        )
        if not persisted:
            return
        validate_allocation_transition(persisted.status, self.status)
        changed = [
            fieldname
            for fieldname in IMMUTABLE_FIELDS
            if _comparable(persisted.get(fieldname)) != _comparable(self.get(fieldname))
        ]
        if changed:
            raise GSFError(
                f"GSF Allocation request fields are immutable: {', '.join(changed)}",
                "IDEMPOTENCY_CONFLICT",
            )

    def warehouse_set(self) -> frozenset[str]:
        return frozenset(
            line.strip() for line in (self.allowed_warehouses or "").splitlines() if line.strip()
        )

    def on_trash(self) -> None:
        if frappe.flags.in_uninstall:
            return
        frappe.throw(
            "GSF Allocation is audit evidence and cannot be deleted", title="ALLOCATION_CONFLICT"
        )
