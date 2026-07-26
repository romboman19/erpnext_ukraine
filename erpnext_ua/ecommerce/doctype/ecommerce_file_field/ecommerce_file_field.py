from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_ua.ecommerce.base.serializers.transforms import (
    is_registered_custom_transform,
)


class EcommerceFileField(Document):
    def validate(self):
        self.erp_fieldname = (self.erp_fieldname or "").strip()
        self.external_column = (self.external_column or "").strip()
        self.transform = (self.transform or "none").strip()
        if not self.erp_fieldname or not self.external_column:
            frappe.throw(_("ERP Field and External Field are required"))
        if self.transform not in {"none", "html_strip", "number_2dp", "custom-method-path"}:
            frappe.throw(_("Unsupported ecommerce file transform"))
        if self.transform == "custom-method-path":
            self.custom_transform_method = (self.custom_transform_method or "").strip()
            if not is_registered_custom_transform(self.custom_transform_method):
                frappe.throw(
                    _("Ecommerce transform is not registered: {0}").format(
                        self.custom_transform_method or "?"
                    )
                )
        else:
            self.custom_transform_method = ""
