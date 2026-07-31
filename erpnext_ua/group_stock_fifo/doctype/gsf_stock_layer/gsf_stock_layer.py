"""Controller for GSF Stock Layer (§9.9).

A layer is the immutable identity of one primary receipt. It deliberately has
no `current_company` field: one layer can sit in several company/warehouse
positions at once, and the answer to "where is it now" belongs to
`GSF Layer Balance` (§9.10). Its cost never lives here either — ADR-002 forbids
reading a layer's value from the registry instead of from the ledger.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime

from erpnext_ua.group_stock_fifo.services.domain import (
    LAYER_PENDING,
    GSFError,
    LayerOrigin,
    check_layer_immutability,
    layer_identity,
    validate_layer_transition,
    validate_tracking_identity,
)


class GSFStockLayer(Document):
    def autoname(self) -> None:
        """§11.3: the name is the identity, so reprocessing a row finds this row."""
        self.name = layer_identity(self._origin(), site_id=frappe.local.site)

    def validate(self) -> None:
        try:
            validate_tracking_identity(
                tracking_type=self.tracking_type,
                batch_no=self.batch_no,
                serial_numbers=self.serial_list(),
                qty=self.original_received_qty,
            )
            before = self.get_doc_before_save()
            if before:
                validate_layer_transition(before.layer_status, self.layer_status)
                check_layer_immutability(
                    _identity_snapshot(before),
                    _identity_snapshot(self),
                    previous_status=before.layer_status,
                )
        except GSFError as error:
            frappe.throw(str(error), title=error.code)

        if self.is_new() and self.layer_status != LAYER_PENDING:
            frappe.throw(
                "A layer is created PENDING and only opens once its ledger entry exists",
                title="MANUAL_REVIEW_REQUIRED",
            )

    def serial_list(self) -> tuple[str, ...]:
        return tuple(line.strip() for line in (self.serial_numbers or "").splitlines() if line.strip())

    def _origin(self) -> LayerOrigin:
        return LayerOrigin(
            company_group=self.company_group,
            origin_doctype=self.origin_doctype,
            origin_document=self.origin_document,
            origin_row_name=self.origin_row_name,
            item_code=self.item_code,
            batch_no=self.batch_no,
            serial_numbers=self.serial_list(),
        )


def _identity_snapshot(doc) -> dict[str, object]:
    """The §9.9 immutable set, read the same way from a saved and an unsaved doc."""
    snapshot = {
        field: doc.get(field)
        for field in (
            "company_group",
            "item_code",
            "origin_company",
            "origin_doctype",
            "origin_document",
            "origin_row_name",
            "tracking_type",
            "batch_no",
            "return_origin_layer",
            "lineage_root_layer",
        )
    }
    # The saved document carries a `datetime`, the edited one usually a string.
    # Comparing them raw would report every save as an identity change.
    received = doc.get("original_received_datetime")
    snapshot["original_received_datetime"] = get_datetime(received) if received else None
    snapshot["serial_numbers"] = tuple(
        line.strip() for line in (doc.get("serial_numbers") or "").splitlines() if line.strip()
    )
    return snapshot
