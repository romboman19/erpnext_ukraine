import frappe


def execute(filters=None):
    filters = frappe._dict(filters or {})
    query = {}
    if filters.scope:
        query["scope"] = filters.scope
    if filters.status:
        query["reconciliation_status"] = filters.status
    columns = [
        {"fieldname": "name", "label": "Рахунок", "fieldtype": "Link", "options": "UA Loyalty Account", "width": 150},
        {"fieldname": "customer", "label": "Клієнт", "fieldtype": "Link", "options": "Customer", "width": 180},
        {"fieldname": "scope", "label": "Область", "fieldtype": "Link", "options": "UA Loyalty Scope", "width": 140},
        {"fieldname": "reconciliation_status", "label": "Стан", "fieldtype": "Data", "width": 120},
        {"fieldname": "last_reconciled_at", "label": "Остання звірка", "fieldtype": "Datetime", "width": 160},
        {"fieldname": "marketing_balance", "label": "Баланс", "fieldtype": "Currency", "width": 110},
        {"fieldname": "reserved_balance", "label": "Reserved", "fieldtype": "Currency", "width": 110},
        {"fieldname": "metric_balance", "label": "Метрика", "fieldtype": "Currency", "width": 110},
    ]
    return columns, frappe.get_list(
        "UA Loyalty Account",
        filters=query,
        fields=[column["fieldname"] for column in columns],
        order_by="reconciliation_status desc, modified desc",
        limit=5000,
    )
