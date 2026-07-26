import frappe

from .service import sync_sales_invoice_rz_statuses


def sync_track_statuses():
    result = sync_sales_invoice_rz_statuses(limit=100)
    if not result.get("ok"):
        frappe.db.commit()
        raise RuntimeError("One or more Rozetka Delivery status updates failed")
    return result
