app_name = "ukrainian_integrations"
app_title = "Ukrainian Integrations"
app_publisher = "HUNTER.rv"
app_description = "UA shipment, payments, PBX/SMS and ecommerce integrations for ERPNext"
app_email = "it@hunter.rv.ua"
app_license = "MIT"
required_apps = ["erpnext"]

doctype_js = {
    "Sales Invoice": [
        "public/js/sales_invoice_shipment_actions.js",
        "public/js/sales_invoice_vitalpbx_actions.js",
    ],
    "Customer": "public/js/customer_vitalpbx_actions.js",
}

scheduler_events = {
    "cron": {
        "*/30 * * * *": [
            "ukrainian_integrations.shipment.nova_poshta.scheduler.sync_ttn_statuses",
            "ukrainian_integrations.shipment.ukr_poshta.scheduler.sync_ttn_statuses",
            "ukrainian_integrations.payments.core.bank_import_scheduler.run_all_bank_imports",
        ],
        "*/10 * * * *": [
            "ukrainian_integrations.ecommerce.core.scheduler.cron_sync_orders",
        ],
        "*/20 * * * *": [
            "ukrainian_integrations.ecommerce.core.scheduler.cron_sync_stock",
        ],
    }
}
