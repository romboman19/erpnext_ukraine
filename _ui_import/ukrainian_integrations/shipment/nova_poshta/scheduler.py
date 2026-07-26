import frappe

from .service import sync_sales_invoice_ttn_statuses


def sync_ttn_statuses():
    result = sync_sales_invoice_ttn_statuses(limit=100)
    if not result.get("ok"):
        frappe.db.commit()
        raise RuntimeError("One or more Nova Poshta status updates failed")
    return result
