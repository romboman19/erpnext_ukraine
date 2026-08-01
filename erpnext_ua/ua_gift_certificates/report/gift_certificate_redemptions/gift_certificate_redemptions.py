import frappe


def execute(filters=None):
    filters = frappe._dict(filters or {})
    query = {"certificate": filters.certificate} if filters.certificate else {}
    if filters.from_date and filters.to_date:
        query["creation"] = ("between", [filters.from_date, filters.to_date])
    columns = [
        {"fieldname": "creation", "label": "Date", "fieldtype": "Datetime", "width": 150},
        {
            "fieldname": "certificate",
            "label": "Certificate",
            "fieldtype": "Link",
            "options": "UA Gift Certificate",
            "width": 150,
        },
        {
            "fieldname": "sales_invoice",
            "label": "Sales Invoice",
            "fieldtype": "Link",
            "options": "Sales Invoice",
            "width": 150,
        },
        {"fieldname": "item_code", "label": "Item", "fieldtype": "Link", "options": "Item", "width": 140},
        {"fieldname": "certificate_amount", "label": "Certificate Amount", "fieldtype": "Currency", "width": 130},
        {"fieldname": "paid_component_amount", "label": "Paid", "fieldtype": "Currency", "width": 110},
        {"fieldname": "promotional_component_amount", "label": "Promotional", "fieldtype": "Currency", "width": 120},
        {
            "fieldname": "redeemer_fop_profile",
            "label": "Redeemer FOP",
            "fieldtype": "Link",
            "options": "FOP Profile",
            "width": 140,
        },
    ]
    return columns, frappe.get_list(
        "UA Gift Certificate Redemption Allocation",
        filters=query,
        fields=[column["fieldname"] for column in columns],
        order_by="creation desc",
        limit=5000,
    )
