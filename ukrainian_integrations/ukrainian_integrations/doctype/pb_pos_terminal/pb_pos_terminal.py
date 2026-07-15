import ipaddress

import frappe
from frappe import _
from frappe.model.document import Document


class PBPOSTerminal(Document):
    def validate(self):
        try:
            ipaddress.ip_address((self.ip_address or "").strip())
        except ValueError:
            frappe.throw(_("PB POS Terminal requires a valid IPv4 or IPv6 address"))
        port = int(self.tcp_port or 0)
        if port < 1 or port > 65535:
            frappe.throw(_("PB POS terminal port must be between 1 and 65535"))
