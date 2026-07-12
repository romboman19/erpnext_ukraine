app_name = "ukrainian_integrations"
app_title = "ERPNext Ukraine Integrations"
app_publisher = "HUNTER.rv"
app_description = "External connectors for ERPNext Ukraine: delivery, banks, online payments, marketplaces, PBX and SMS"
app_email = "it@hunter.rv.ua"
app_license = "MIT"
required_apps = ["erpnext"]

app_include_js = "/assets/ukrainian_integrations/js/vitalpbx_popup_listener.js"

doctype_js = {
    "Sales Invoice": [
        "public/js/sales_invoice_shipment_actions.js",
        "public/js/sales_invoice_vitalpbx_actions.js",
    ],
    "Customer": "public/js/customer_vitalpbx_actions.js",
    "NP Sender Profile": "public/js/np_sender_profile_actions.js",
    "UP Sender Profile": "public/js/up_sender_profile_actions.js",
    "TurboSMS Settings": "public/js/turbosms_settings_actions.js",
    "Monobank Settings": "public/js/monobank_settings_actions.js",
    "PrivatBank Settings": "public/js/privatbank_settings_actions.js",
    "LiqPay Settings": "public/js/liqpay_settings_actions.js",
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

after_migrate = [
    "ukrainian_integrations.pbx_sms.vitalpbx.custom_fields.ensure_user_vitalpbx_extension_field",
]


doctype_list_js = {
    "NP Sender Profile": "public/js/np_sender_profile_list.js",
    "UP Sender Profile": "public/js/up_sender_profile_list.js",
}
