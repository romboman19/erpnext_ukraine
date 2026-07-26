from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

FINAL_STATUSES = {"Processed"}


class EcommerceFileExchange(Document):
    def validate(self):
        if not self.is_new() and self.has_value_changed("exchange_file"):
            old_status = frappe.db.get_value(self.doctype, self.name, "status")
            if old_status in FINAL_STATUSES:
                frappe.throw(_("A processed exchange file is immutable; create a new import"))
        if self.direction == "Import" and self.entity != "Orders":
            frappe.throw(_("Only order file imports are supported"))
        if self.direction == "Import" and self.file_format != "XML":
            frappe.throw(_("Order file imports must use XML"))
        if self.direction == "Import" and self.profile != "ERPNext Exchange XML v1":
            frappe.throw(_("Order file imports must use ERPNext Exchange XML v1"))
        if self.direction == "Export" and self.entity not in {"Catalog", "Prices and Stock"}:
            frappe.throw(_("Only catalog and stock file exports are supported"))
