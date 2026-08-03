import frappe


def execute(filters=None):
    filters = frappe._dict(filters or {})
    query = {key: filters[key] for key in ("status", "program", "network") if filters.get(key)}
    columns = [
        {"fieldname": "public_serial", "label": "Serial", "fieldtype": "Data", "width": 150},
        {
            "fieldname": "program",
            "label": "Program",
            "fieldtype": "Link",
            "options": "UA Gift Certificate Program",
            "width": 150,
        },
        {"fieldname": "status", "label": "Status", "fieldtype": "Data", "width": 130},
        {"fieldname": "face_value", "label": "Face", "fieldtype": "Currency", "width": 110},
        {"fieldname": "sale_price", "label": "Sale Price", "fieldtype": "Currency", "width": 110},
        {"fieldname": "paid_balance", "label": "Paid", "fieldtype": "Currency", "width": 110},
        {"fieldname": "promotional_balance", "label": "Promotional", "fieldtype": "Currency", "width": 120},
        {"fieldname": "available_balance", "label": "Available", "fieldtype": "Currency", "width": 110},
        {"fieldname": "valid_until", "label": "Valid Until", "fieldtype": "Date", "width": 100},
    ]
    rows = frappe.get_list(
        "UA Gift Certificate",
        filters=query,
        fields=[column["fieldname"] for column in columns],
        order_by="creation desc",
        limit=5000,
    )
    return columns, rows
