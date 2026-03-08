app_name = "ukrainian_integrations"
app_title = "Ukrainian Integrations"
app_publisher = "HUNTER.rv"
app_description = "UA shipment, payments, PBX/SMS and ecommerce integrations for ERPNext"
app_email = "it@hunter.rv.ua"
app_license = "MIT"
required_apps = ["erpnext"]

scheduler_events = {
    "cron": {
        "*/30 * * * *": [
            "ukrainian_integrations.shipment.nova_poshta.scheduler.sync_ttn_statuses",
            "ukrainian_integrations.shipment.ukr_poshta.scheduler.sync_ttn_statuses",
            "ukrainian_integrations.payments.privatbank.scheduler.run_auto_import",
            "ukrainian_integrations.payments.monobank.scheduler.run_auto_import",
        ],
        "*/10 * * * *": [
            "ukrainian_integrations.ecommerce.core.scheduler.sync_orders_all",
        ],
    }
}
