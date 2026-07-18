from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

ENTITY_DIRECTIONS = {
    "Products": "Export",
    "Prices": "Export",
    "Stock": "Export",
    "Photos": "Export",
    "Orders": "Import",
    "Customers": "Import",
}


class EcommerceSyncEntityConfig(Document):
    def validate(self):
        if self.entity not in ENTITY_DIRECTIONS:
            frappe.throw(_("Unsupported ecommerce sync entity"))
        self.direction = ENTITY_DIRECTIONS[self.entity]
        if self.method not in {"File", "API", "Disabled"}:
            frappe.throw(_("Unsupported ecommerce synchronization method"))
        if self.method == "Disabled":
            self.enabled = 0
        if int(self.enabled or 0):
            self.interval_minutes = int(self.interval_minutes or 0)
            if not 1 <= self.interval_minutes <= 43_200:
                frappe.throw(_("Synchronization interval must be between 1 and 43200 minutes"))
            if self.method == "File":
                if self.file_format not in {"CSV", "XML", "YML"} or not self.file_layout:
                    frappe.throw(_("Enabled File synchronization requires a format and layout"))
                layout_format = frappe.db.get_value("Ecommerce File Layout", self.file_layout, "format")
                if layout_format and layout_format != self.file_format:
                    frappe.throw(_("File layout format does not match the synchronization format"))
        if self.method != "File":
            self.file_format = ""
            self.file_layout = ""
