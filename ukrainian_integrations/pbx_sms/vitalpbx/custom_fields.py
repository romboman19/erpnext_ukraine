from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def ensure_user_vitalpbx_extension_field():
    custom_fields = {
        "User": [
            {
                "fieldname": "vitalpbx_extension",
                "label": "VitalPBX Extension",
                "fieldtype": "Data",
                "insert_after": "phone",
                "hidden": 0,
                "read_only": 0,
                "no_copy": 1,
            }
        ]
    }
    create_custom_fields(custom_fields, update=True)
