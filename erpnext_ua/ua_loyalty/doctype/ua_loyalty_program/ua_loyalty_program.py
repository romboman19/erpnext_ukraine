import frappe
from frappe.model.document import Document


class UALoyaltyProgram(Document):
    def validate(self):
        if not self.is_new() and self.get_db_value("published_snapshot_hash"):
            protected = (
                "scope",
                "rate_timing",
                "credit_consumption_mode",
                "expiry_writeoff_mode",
                "amount_base_mode",
                "max_redemption_percent",
                "minimum_redemption_amount",
                "minimum_cash_remainder",
                "activation_mode",
                "activation_days",
                "expiry_mode",
                "bonus_validity_days",
                "returned_bonus_validity_days",
                "extra_bonus_percent",
            )
            if any(self.has_value_changed(fieldname) for fieldname in protected) or self.has_value_changed("tiers"):
                frappe.throw("Опубліковані правила незмінні; створіть нову версію програми")
        ordered = sorted(self.tiers, key=lambda row: row.threshold_amount)
        thresholds = [row.threshold_amount for row in ordered]
        if len(thresholds) != len(set(thresholds)):
            frappe.throw("Пороги програми мають бути унікальними")
        for sequence, row in enumerate(ordered, 1):
            if row.earn_percent < 0:
                frappe.throw("Бонусний відсоток не може бути від’ємним")
            row.sequence = sequence
        self.tiers = ordered
        self.allow_negative_balance_from_reversal = 1
