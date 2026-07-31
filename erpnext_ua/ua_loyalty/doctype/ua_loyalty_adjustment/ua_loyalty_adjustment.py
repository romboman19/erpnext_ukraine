from __future__ import annotations

import frappe
from frappe.model.document import Document

from erpnext_ua.ua_loyalty.domain.money import ZERO, decimal
from erpnext_ua.ua_loyalty.services.account_service import lock_account
from erpnext_ua.ua_loyalty.services.ledger_service import append_ledger, append_metric
from erpnext_ua.ua_loyalty.services.settings import settings


class UALoyaltyAdjustment(Document):
    def before_insert(self):
        self.requested_by = self.requested_by or frappe.session.user
        self.status = self.status or "DRAFT"
        self.idempotency_key = self.idempotency_key or f"adjustment:{frappe.generate_hash(length=24)}"

    def validate(self):
        if decimal(self.amount) <= ZERO:
            frappe.throw("Сума коригування має бути більшою за нуль")
        if self.docstatus == 0 and self.status == "POSTED":
            frappe.throw("Draft-коригування не може мати стан POSTED")

    def before_submit(self):
        if self.status != "APPROVED" or not self.approved_by:
            frappe.throw("Коригування потрібно погодити перед posting")
        threshold = decimal(settings().dual_control_threshold or 0)
        if decimal(self.amount) >= threshold and self.requested_by == self.approved_by:
            frappe.throw("Requester і approver мають бути різними для цієї суми")
        account = lock_account(self.account)
        delta = decimal(self.amount)
        if self.operation in {"DEBIT", "METRIC_DECREASE"}:
            delta = -delta
        common = {
            "effective_datetime": self.effective_datetime,
            "expires_at": self.expires_at,
            "reason_code": self.reason_code,
            "metadata_json": frappe.as_json({"comment": self.comment}),
            "lot_kind": "MANUAL",
        }
        if self.operation in {"CREDIT", "DEBIT"}:
            entry = append_ledger(
                account,
                entry_type="MANUAL_CREDIT" if delta > ZERO else "MANUAL_DEBIT",
                active_delta=delta,
                idempotency_key=self.idempotency_key,
                source_doctype=self.doctype,
                source_name=self.name,
                values=common,
            )
            self.posted_ledger_entry = entry.name
        else:
            entry = append_metric(
                account,
                entry_type="MANUAL_ADJUSTMENT",
                delta=delta,
                idempotency_key=self.idempotency_key,
                source_doctype=self.doctype,
                source_name=self.name,
                values=common,
            )
            self.posted_metric_entry = entry.name
        self.status = "POSTED"

    def before_cancel(self):
        frappe.throw("Проведене коригування не скасовується; створіть inverse adjustment")
