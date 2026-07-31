import frappe


class ImmutableServiceDocument:
    def validate(self):
        if not getattr(frappe.flags, "ua_loyalty_service", False):
            frappe.throw(f"{self.doctype} створюється лише через UA Loyalty service")
        if not self.is_new():
            frappe.throw(f"{self.doctype} є append-only")

    def on_trash(self):
        if not frappe.flags.in_uninstall:
            frappe.throw(f"{self.doctype} є аудиторським записом і не може бути видалений")
