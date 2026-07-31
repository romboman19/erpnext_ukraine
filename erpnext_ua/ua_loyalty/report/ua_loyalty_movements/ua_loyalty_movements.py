import frappe


def execute(filters=None):
    filters = frappe._dict(filters or {})
    query = {}
    for fieldname in ("account", "scope"):
        if filters.get(fieldname):
            query[fieldname] = filters[fieldname]
    if filters.from_date and filters.to_date:
        query["posting_datetime"] = ("between", [filters.from_date, filters.to_date])
    elif filters.from_date:
        query["posting_datetime"] = (">=", filters.from_date)
    elif filters.to_date:
        query["posting_datetime"] = ("<=", filters.to_date)
    columns = [
        {"fieldname": "posting_datetime", "label": "Дата", "fieldtype": "Datetime", "width": 150},
        {
            "fieldname": "account",
            "label": "Рахунок",
            "fieldtype": "Link",
            "options": "UA Loyalty Account",
            "width": 150,
        },
        {"fieldname": "scope", "label": "Область", "fieldtype": "Link", "options": "UA Loyalty Scope", "width": 130},
        {"fieldname": "entry_type", "label": "Тип", "fieldtype": "Data", "width": 170},
        {"fieldname": "active_delta", "label": "Активний рух", "fieldtype": "Currency", "width": 120},
        {"fieldname": "pending_delta", "label": "Pending рух", "fieldtype": "Currency", "width": 120},
        {"fieldname": "balance_after", "label": "Баланс після", "fieldtype": "Currency", "width": 120},
        {"fieldname": "source_doctype", "label": "Тип джерела", "fieldtype": "Data", "width": 130},
        {
            "fieldname": "source_name",
            "label": "Джерело",
            "fieldtype": "Dynamic Link",
            "options": "source_doctype",
            "width": 170,
        },
    ]
    rows = frappe.get_list(
        "UA Loyalty Ledger Entry",
        filters=query,
        fields=[column["fieldname"] for column in columns],
        order_by="posting_datetime desc, name desc",
        limit=5000,
    )
    return columns, rows
