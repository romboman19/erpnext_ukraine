import frappe


def execute():
    if not frappe.db.exists("DocType", "PRRO Fiscalization Job"):
        return
    from erpnext_ua.ua_fiscal.outbox import ensure_sales_invoice_job

    for name in _untracked_invoices():
        try:
            ensure_sales_invoice_job(name)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"PRRO outbox migration {name}",
            )


def _untracked_invoices() -> list[str]:
    ecommerce_filter = ""
    if frappe.db.has_column("Sales Invoice", "ua_ecommerce_channel"):
        ecommerce_filter = (
            "or (coalesce(si.ua_ecommerce_channel, '') != '' and abs(coalesce(si.outstanding_amount, 0)) <= 0.01)"
        )
    return frappe.db.sql(
        f"""select distinct si.name
        from `tabSales Invoice` si
        left join `tabPRRO Fiscalization Job` job on job.sales_invoice = si.name
        left join `tabPRRO Receipt` receipt
            on receipt.sales_invoice = si.name
            and receipt.status in ('Fiscalized', 'Offline')
        where si.docstatus = 1
            and job.name is null
            and receipt.name is null
            and (si.is_pos = 1 {ecommerce_filter})
        order by si.creation asc""",
        pluck=True,
    )
