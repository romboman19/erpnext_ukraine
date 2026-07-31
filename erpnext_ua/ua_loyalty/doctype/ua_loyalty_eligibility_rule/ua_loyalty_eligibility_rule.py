import frappe
from frappe.model.document import Document


class UALoyaltyEligibilityRule(Document):
    def validate(self):
        if frappe.db.get_value("UA Loyalty Program", self.program, "published_snapshot_hash"):
            frappe.throw("Eligibility rules опублікованої версії незмінні")
        for fieldname in ("earn_percent_override", "extra_bonus_percent", "max_redemption_percent_override"):
            value = self.get(fieldname)
            if value not in (None, "") and not 0 <= value <= 100:
                frappe.throw(f"{fieldname} має бути від 0 до 100")
