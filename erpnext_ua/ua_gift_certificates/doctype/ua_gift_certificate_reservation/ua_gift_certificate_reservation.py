import frappe
from frappe.model.document import Document

from erpnext_ua.ua_gift_certificates.constants import WRITE_FLAG


class UAGiftCertificateReservation(Document):
    def validate(self):
        if not getattr(frappe.flags, WRITE_FLAG, False):
            frappe.throw("Reservations are changed only through domain services")

    def on_trash(self):
        if not frappe.flags.in_uninstall:
            frappe.throw("Reservations cannot be deleted")
