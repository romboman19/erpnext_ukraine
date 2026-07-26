from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_ua.ecommerce.base.serializers.common import encoding
from erpnext_ua.ecommerce.base.serializers.xml import validate_xml_name


class EcommerceFileLayout(Document):
    def validate(self):
        self.layout_name = (self.layout_name or "").strip()
        self.format = (self.format or "").strip().upper()
        if self.format not in {"CSV", "XML", "YML"}:
            frappe.throw(_("Ecommerce layout format must be CSV, XML or YML"))
        try:
            encoding(self)
        except ValueError as exc:
            frappe.throw(_(str(exc)))
        fields = list(self.get("fields") or [])
        if not fields:
            frappe.throw(_("Ecommerce file layout requires at least one field"))
        for field in fields:
            # Frappe validates child schema but does not call child controller hooks.
            field.run_method("validate")
        if self.format == "CSV":
            self.delimiter = self.delimiter or ","
            if len(self.delimiter) != 1 or self.delimiter in {"\r", "\n", "\0"}:
                frappe.throw(_("CSV delimiter must be one printable character"))
            self.root_element = ""
            self.item_element = ""
        else:
            try:
                self.root_element = validate_xml_name(self.root_element, "Root Element")
                self.item_element = validate_xml_name(self.item_element, "Item Element")
            except ValueError as exc:
                frappe.throw(_(str(exc)))
            for field in fields:
                try:
                    validate_xml_name(field.external_column, "External Field")
                except ValueError as exc:
                    frappe.throw(_(str(exc)))
        erp_names = [(field.erp_fieldname or "").strip() for field in fields]
        external_names = [(field.external_column or "").strip() for field in fields]
        if len(erp_names) != len(set(erp_names)):
            frappe.throw(_("ERP fields must be unique within an ecommerce layout"))
        if len(external_names) != len(set(external_names)):
            frappe.throw(_("External fields must be unique within an ecommerce layout"))
