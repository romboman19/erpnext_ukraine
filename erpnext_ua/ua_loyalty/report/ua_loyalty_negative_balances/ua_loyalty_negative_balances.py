import frappe


def execute(filters=None):
    filters = frappe._dict(filters or {})
    query = {"marketing_balance": ("<", 0)}
    if filters.scope:
        query["scope"] = filters.scope
    columns = [
        {"fieldname": "name", "label": "Рахунок", "fieldtype": "Link", "options": "UA Loyalty Account", "width": 150},
        {"fieldname": "customer", "label": "Клієнт", "fieldtype": "Link", "options": "Customer", "width": 180},
        {"fieldname": "scope", "label": "Область", "fieldtype": "Link", "options": "UA Loyalty Scope", "width": 140},
        {"fieldname": "marketing_balance", "label": "Баланс", "fieldtype": "Currency", "width": 120},
        {"fieldname": "debt_balance", "label": "Debt", "fieldtype": "Currency", "width": 120},
        {
            "fieldname": "last_ledger_entry",
            "label": "Останній рух",
            "fieldtype": "Link",
            "options": "UA Loyalty Ledger Entry",
            "width": 150,
        },
    ]
    return columns, frappe.get_list(
        "UA Loyalty Account",
        filters=query,
        fields=[column["fieldname"] for column in columns],
        order_by="marketing_balance asc",
        limit=5000,
    )
