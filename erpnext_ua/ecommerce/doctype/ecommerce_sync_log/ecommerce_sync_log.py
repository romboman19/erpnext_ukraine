from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_ua.integrations.utils.logger import sanitize_text


class EcommerceSyncLog(Document):
    def validate(self):
        if not self.is_new():
            frappe.throw(_("Ecommerce Sync Log is append-only"), frappe.PermissionError)
        self.channel = sanitize_text(self.channel)[:140]
        self.idempotency_key = sanitize_text(self.idempotency_key)[:240]
        self.message = sanitize_text(self.message)[:1000]
        self.payload_ref = sanitize_text(self.payload_ref)[:1000]
        if "://" in (self.payload_ref or ""):
            frappe.throw(_("Payload Reference must be a path or document name, not a URL"))
        if int(self.records_ok or 0) < 0 or int(self.records_failed or 0) < 0:
            frappe.throw(_("Ecommerce sync counters cannot be negative"))

    def on_trash(self):
        frappe.throw(_("Ecommerce Sync Log is append-only"), frappe.PermissionError)
