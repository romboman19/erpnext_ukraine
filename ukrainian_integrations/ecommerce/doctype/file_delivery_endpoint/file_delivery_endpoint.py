from __future__ import annotations

import posixpath

import frappe
from frappe import _
from frappe.model.document import Document

from ukrainian_integrations.ecommerce.base.transport import FileDeliveryTransport
from ukrainian_integrations.utils.security import SYSTEM_ROLES, require_roles
from ukrainian_integrations.utils.validation import validate_hostname


class FileDeliveryEndpoint(Document):
    def validate(self):
        self.endpoint_name = (self.endpoint_name or "").strip()
        self.protocol = (self.protocol or "").strip().upper()
        self.host = validate_hostname(self.host, "File Delivery Endpoint")
        if self.protocol not in {"FTP", "FTPS", "SFTP"}:
            frappe.throw(_("Protocol must be FTP, FTPS or SFTP"))
        default_port = 22 if self.protocol == "SFTP" else 21
        self.port = int(self.port or default_port)
        if not 1 <= self.port <= 65535:
            frappe.throw(_("Port must be between 1 and 65535"))
        self.username = (self.username or "").strip()
        if not self.username:
            frappe.throw(_("File delivery username is required"))
        self.base_path = (self.base_path or "/").strip().replace("\\", "/")
        if ".." in self.base_path.split("/") or "\0" in self.base_path:
            frappe.throw(_("Base Path cannot traverse directories"))
        self.base_path = "/" + self.base_path.strip("/") if self.base_path.strip("/") else "/"
        if posixpath.normpath(self.base_path) != self.base_path:
            frappe.throw(_("Base Path must be normalized"))
        password = self.get_password("password", raise_exception=False)
        ssh_key = self.get_password("ssh_key", raise_exception=False)
        if self.protocol in {"FTP", "FTPS"} and not password:
            frappe.throw(_("FTP/FTPS endpoint requires a password"))
        if self.protocol == "SFTP" and not (password or ssh_key):
            frappe.throw(_("SFTP endpoint requires a password or SSH private key"))

    @frappe.whitelist(methods=["POST"])
    def test_connection(self):
        require_roles(*SYSTEM_ROLES)
        self.check_permission("write")
        return FileDeliveryTransport(self).test_connection()
