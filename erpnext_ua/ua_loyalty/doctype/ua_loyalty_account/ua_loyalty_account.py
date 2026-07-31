import frappe
from frappe.model.document import Document


class UALoyaltyAccount(Document):
    def validate(self):
        duplicate = frappe.db.exists(
            "UA Loyalty Account", {"customer": self.customer, "scope": self.scope, "name": ("!=", self.name or "")}
        )
        if duplicate:
            frappe.throw("Клієнт уже має рахунок у цій області")
        if not getattr(frappe.flags, "ua_loyalty_service", False):
            protected = {"metric_balance", "marketing_balance", "pending_balance", "reserved_balance"}
            if not self.is_new() and any(self.has_value_changed(field) for field in protected):
                frappe.throw("Баланс рахунку змінюється лише через UA Loyalty service")
        self.redeemable_balance = max(0, (self.marketing_balance or 0) - (self.reserved_balance or 0))
        self.debt_balance = max(0, -(self.marketing_balance or 0))

    def on_trash(self):
        if frappe.flags.in_uninstall:
            return
        if frappe.db.exists("UA Loyalty Ledger Entry", {"account": self.name}):
            frappe.throw("Рахунок із рухами не можна видалити")
