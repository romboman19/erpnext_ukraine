from __future__ import annotations

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def ensure_integration_custom_fields():
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
        ],
        "Bank Transaction": [
            {
                "fieldname": "ua_integration_key",
                "label": "UA Integration Key",
                "fieldtype": "Data",
                "insert_after": "bank_account_no",
                "read_only": 1,
                "unique": 1,
                "no_copy": 1,
                "length": 80,
            }
        ],
        "Sales Invoice": [
            {"fieldname": "ua_integrations_section", "label": "Ukrainian Integrations", "fieldtype": "Section Break", "insert_after": "remarks", "collapsible": 1},
            {"fieldname": "np_ttn_number", "label": "Nova Poshta TTN", "fieldtype": "Data", "insert_after": "ua_integrations_section", "read_only": 1, "no_copy": 1},
            {"fieldname": "np_ttn_ref", "label": "Nova Poshta TTN Ref", "fieldtype": "Data", "insert_after": "np_ttn_number", "read_only": 1, "no_copy": 1},
            {"fieldname": "np_status", "label": "Nova Poshta Status", "fieldtype": "Data", "insert_after": "np_ttn_ref", "read_only": 1, "no_copy": 1},
            {"fieldname": "np_sender_profile", "label": "Nova Poshta Sender Profile", "fieldtype": "Link", "options": "NP Sender Profile", "insert_after": "np_status", "read_only": 1, "no_copy": 1},
            {"fieldname": "np_last_sync_at", "label": "Nova Poshta Last Sync", "fieldtype": "Datetime", "insert_after": "np_sender_profile", "read_only": 1, "no_copy": 1},
            {"fieldname": "up_barcode", "label": "Ukrposhta Barcode", "fieldtype": "Data", "insert_after": "np_last_sync_at", "read_only": 1, "no_copy": 1},
            {"fieldname": "up_shipment_id", "label": "Ukrposhta Shipment ID", "fieldtype": "Data", "insert_after": "up_barcode", "read_only": 1, "no_copy": 1},
            {"fieldname": "up_status", "label": "Ukrposhta Status", "fieldtype": "Data", "insert_after": "up_shipment_id", "read_only": 1, "no_copy": 1},
            {"fieldname": "up_sender_profile", "label": "Ukrposhta Sender Profile", "fieldtype": "Link", "options": "UP Sender Profile", "insert_after": "up_status", "read_only": 1, "no_copy": 1},
            {"fieldname": "up_last_sync_at", "label": "Ukrposhta Last Sync", "fieldtype": "Datetime", "insert_after": "up_sender_profile", "read_only": 1, "no_copy": 1},
            {"fieldname": "rz_track_id", "label": "Rozetka Delivery Track ID", "fieldtype": "Data", "insert_after": "up_last_sync_at", "read_only": 1, "unique": 1, "no_copy": 1, "length": 64},
            {"fieldname": "rz_status_code", "label": "Rozetka Delivery Status Code", "fieldtype": "Data", "insert_after": "rz_track_id", "read_only": 1, "no_copy": 1},
            {"fieldname": "rz_status", "label": "Rozetka Delivery Status", "fieldtype": "Data", "insert_after": "rz_status_code", "read_only": 1, "no_copy": 1},
            {"fieldname": "rz_sender_profile", "label": "Rozetka Delivery Sender Profile", "fieldtype": "Link", "options": "RZ Delivery Sender Profile", "insert_after": "rz_status", "read_only": 1, "no_copy": 1},
            {"fieldname": "rz_last_sync_at", "label": "Rozetka Delivery Last Sync", "fieldtype": "Datetime", "insert_after": "rz_sender_profile", "read_only": 1, "no_copy": 1},
            {"fieldname": "rz_shipping_cost", "label": "Rozetka Delivery Shipping Cost", "fieldtype": "Currency", "insert_after": "rz_last_sync_at", "read_only": 1, "no_copy": 1},
            {"fieldname": "rz_estimated_delivery_date", "label": "Rozetka Delivery Estimated Date", "fieldtype": "Date", "insert_after": "rz_shipping_cost", "read_only": 1, "no_copy": 1},
            {"fieldname": "rz_payment_fee", "label": "Rozetka Delivery Payment Fee", "fieldtype": "Currency", "insert_after": "rz_estimated_delivery_date", "read_only": 1, "no_copy": 1},
            {"fieldname": "pb_pos_status", "label": "Privat POS Status", "fieldtype": "Data", "insert_after": "rz_payment_fee", "read_only": 1, "no_copy": 1},
            {"fieldname": "pb_pos_rrn", "label": "Privat POS RRN", "fieldtype": "Data", "insert_after": "pb_pos_status", "read_only": 1, "no_copy": 1},
            {"fieldname": "pb_pos_invoice_number", "label": "Privat POS Invoice", "fieldtype": "Data", "insert_after": "pb_pos_rrn", "read_only": 1, "no_copy": 1},
            {"fieldname": "pb_pos_card_mask", "label": "Privat POS Card Mask", "fieldtype": "Data", "insert_after": "pb_pos_invoice_number", "read_only": 1, "no_copy": 1},
            {"fieldname": "liqpay_status", "label": "LiqPay Status", "fieldtype": "Data", "insert_after": "pb_pos_card_mask", "read_only": 1, "no_copy": 1},
            {"fieldname": "liqpay_transaction_id", "label": "LiqPay Transaction ID", "fieldtype": "Data", "insert_after": "liqpay_status", "read_only": 1, "no_copy": 1},
            {"fieldname": "liqpay_paid_amount", "label": "LiqPay Paid Amount", "fieldtype": "Currency", "insert_after": "liqpay_transaction_id", "read_only": 1, "no_copy": 1},
        ],
        "Sales Order": [
            {"fieldname": "ua_external_order_key", "label": "UA External Order Key", "fieldtype": "Data", "insert_after": "po_no", "read_only": 1, "unique": 1, "no_copy": 1, "length": 140}
        ],
        "Customer": [
            {"fieldname": "ua_external_customer_key", "label": "UA External Customer Key", "fieldtype": "Data", "insert_after": "customer_name", "read_only": 1, "unique": 1, "no_copy": 1, "length": 80}
        ],
    }
    create_custom_fields(custom_fields, update=True)


def ensure_user_vitalpbx_extension_field():
    # Backward-compatible hook name retained for existing installations.
    ensure_integration_custom_fields()
