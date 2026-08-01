import frappe

from erpnext_ua.ua_gift_certificates.constants import WRITE_FLAG


class ImmutableServiceDocument:
    def validate(self):
        if not getattr(frappe.flags, WRITE_FLAG, False):
            frappe.throw(f"{self.doctype} is written only by the gift certificate service")
        if not self.is_new():
            frappe.throw(f"{self.doctype} is append-only")

    def on_trash(self):
        if not frappe.flags.in_uninstall:
            frappe.throw(f"{self.doctype} is an audit record and cannot be deleted")
