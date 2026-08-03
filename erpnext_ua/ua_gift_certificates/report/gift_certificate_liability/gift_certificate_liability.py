import frappe


def execute(filters=None):
    filters = frappe._dict(filters or {})
    conditions = []
    values = {}
    for field in ("issuer_company", "issuer_fop_profile"):
        if filters.get(field):
            conditions.append(f"{field} = %({field})s")
            values[field] = filters[field]
    where = " and " + " and ".join(conditions) if conditions else ""
    rows = frappe.db.sql(
        f"""select issuer_company, issuer_fop_profile,
                   sum(paid_balance) paid_liability,
                   sum(promotional_balance) promotional_balance,
                   sum(reserved_balance) reserved_balance,
                   sum(available_balance) available_balance,
                   count(*) certificate_count
            from `tabUA Gift Certificate`
            where status not in ('Cancelled', 'Refunded', 'Replaced') {where}
            group by issuer_company, issuer_fop_profile""",  # nosec B608 -- only fixed allow-listed clauses
        values,
        as_dict=True,
    )
    columns = [
        {"fieldname": "issuer_company", "label": "Company", "fieldtype": "Link", "options": "Company", "width": 160},
        {
            "fieldname": "issuer_fop_profile",
            "label": "FOP",
            "fieldtype": "Link",
            "options": "FOP Profile",
            "width": 150,
        },
        {"fieldname": "paid_liability", "label": "Paid Liability", "fieldtype": "Currency", "width": 130},
        {"fieldname": "promotional_balance", "label": "Promotional", "fieldtype": "Currency", "width": 120},
        {"fieldname": "reserved_balance", "label": "Reserved", "fieldtype": "Currency", "width": 110},
        {"fieldname": "available_balance", "label": "Available", "fieldtype": "Currency", "width": 110},
        {"fieldname": "certificate_count", "label": "Count", "fieldtype": "Int", "width": 90},
    ]
    return columns, rows
