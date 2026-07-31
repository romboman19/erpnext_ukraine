import json

import frappe
from frappe.model.document import Document

from erpnext_ua.ua_loyalty.domain.snapshots import payload_hash


class UALoyaltyImportBatch(Document):
    def validate(self):
        try:
            rows = json.loads(self.source_data or "[]")
        except json.JSONDecodeError as error:
            frappe.throw(f"Некоректний JSON імпорту: {error}")
        if not isinstance(rows, list):
            frappe.throw("JSON імпорту має бути масивом рядків")
        self.source_checksum = payload_hash(rows)
        self.row_count = len(rows)
        if not self.is_new() and self.get_db_value("status") in {"COMPLETED", "DRY_RUN_COMPLETE"}:
            frappe.throw("Завершений import batch є незмінним")

    def on_trash(self):
        if not frappe.flags.in_uninstall and self.status != "DRAFT":
            frappe.throw("Запущений import batch не можна видалити")
