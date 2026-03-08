from .service import sync_sales_invoice_ttn_statuses


def sync_ttn_statuses():
    return sync_sales_invoice_ttn_statuses(limit=100)
