import frappe
from frappe.model.document import Document


class UALoyaltyCardType(Document):
    def validate(self):
        for fieldname in ("extra_bonus_percent", "max_redemption_percent_override"):
            value = self.get(fieldname)
            if value is not None and not 0 <= value <= 100:
                frappe.throw(f"{fieldname} має бути від 0 до 100")
        cursor = self.next_card_type
        visited = {self.name}
        while cursor:
            if cursor in visited:
                frappe.throw("Цикл переходів типів карток заборонено")
            visited.add(cursor)
            cursor = frappe.db.get_value("UA Loyalty Card Type", cursor, "next_card_type")
