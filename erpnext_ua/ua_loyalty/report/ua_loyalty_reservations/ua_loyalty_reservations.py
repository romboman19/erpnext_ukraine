import frappe


def execute(filters=None):
    filters = frappe._dict(filters or {})
    query = {field: filters[field] for field in ("status", "account") if filters.get(field)}
    columns = [
        {
            "fieldname": "name",
            "label": "Reservation",
            "fieldtype": "Link",
            "options": "UA Loyalty Reservation",
            "width": 160,
        },
        {
            "fieldname": "account",
            "label": "Рахунок",
            "fieldtype": "Link",
            "options": "UA Loyalty Account",
            "width": 150,
        },
        {
            "fieldname": "source_name",
            "label": "POS Order",
            "fieldtype": "Dynamic Link",
            "options": "source_doctype",
            "width": 160,
        },
        {"fieldname": "status", "label": "Стан", "fieldtype": "Data", "width": 150},
        {"fieldname": "reserved_amount", "label": "Зарезервовано", "fieldtype": "Currency", "width": 120},
        {"fieldname": "remaining_reserved_amount", "label": "Залишок", "fieldtype": "Currency", "width": 120},
        {"fieldname": "created_at", "label": "Створено", "fieldtype": "Datetime", "width": 150},
        {"fieldname": "expires_at", "label": "Спливає", "fieldtype": "Datetime", "width": 150},
    ]
    fields = [column["fieldname"] for column in columns] + ["source_doctype"]
    return columns, frappe.get_list(
        "UA Loyalty Reservation", filters=query, fields=fields, order_by="created_at desc", limit=5000
    )
