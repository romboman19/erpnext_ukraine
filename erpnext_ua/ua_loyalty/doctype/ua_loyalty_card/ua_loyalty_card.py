import re
import uuid

import frappe
from frappe.model.document import Document


class UALoyaltyCard(Document):
    def before_insert(self):
        self.card_uid = self.card_uid or str(uuid.uuid4())

    def validate(self):
        self.barcode = re.sub(r"\s+", "", self.barcode or "").upper()
        if self.is_primary and frappe.db.exists(
            "UA Loyalty Card", {"account": self.account, "is_primary": 1, "name": ("!=", self.name or "")}
        ):
            frappe.throw("Рахунок уже має основну картку")

    def on_trash(self):
        if not frappe.flags.in_uninstall:
            frappe.throw("Історію карток не можна видаляти")
