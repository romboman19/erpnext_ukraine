app_name = "ukrainian_integrations"
app_title = "Ukrainian Integrations"
app_publisher = "HUNTER.rv"
app_description = "UA shipment, payments, PBX/SMS and ecommerce integrations for ERPNext"
app_email = "it@hunter.rv.ua"
app_license = "MIT"
required_apps = ["erpnext"]

after_install = "ukrainian_integrations.install.after_install"
before_uninstall = "ukrainian_integrations.uninstall.before_uninstall"

app_include_js = "/assets/ukrainian_integrations/js/vitalpbx_popup_listener.js"

doctype_js = {
    "Sales Invoice": [
        "public/js/sales_invoice_shipment_actions.js",
        "public/js/sales_invoice_vitalpbx_actions.js",
    ],
    "Customer": "public/js/customer_vitalpbx_actions.js",
    "NP Sender Profile": "public/js/np_sender_profile_actions.js",
    "UP Sender Profile": "public/js/up_sender_profile_actions.js",
    "RZ Delivery Sender Profile": "public/js/rz_delivery_sender_profile_actions.js",
    "TurboSMS Settings": "public/js/turbosms_settings_actions.js",
    "Monobank Settings": "public/js/monobank_settings_actions.js",
    "PrivatBank Settings": "public/js/privatbank_settings_actions.js",
    "LiqPay Settings": "public/js/liqpay_settings_actions.js",
    "PB POS Terminal": "public/js/pb_pos_terminal_actions.js",
}

scheduler_events = {
    "cron": {
        "*/30 * * * *": [
            "ukrainian_integrations.shipment.nova_poshta.scheduler.sync_ttn_statuses",
            "ukrainian_integrations.shipment.ukr_poshta.scheduler.sync_ttn_statuses",
            "ukrainian_integrations.shipment.rozetka_delivery.scheduler.sync_track_statuses",
            "ukrainian_integrations.payments.core.bank_import_scheduler.run_all_bank_imports",
        ],
        "*/10 * * * *": [
            "ukrainian_integrations.ecommerce.core.scheduler.cron_sync_orders",
        ],
        "*/20 * * * *": [
            "ukrainian_integrations.ecommerce.core.scheduler.cron_sync_stock",
        ],
    },
    "daily": [
        "ukrainian_integrations.utils.logger.purge_old_logs",
    ],
}

after_migrate = [
    "ukrainian_integrations.migrations.after_migrate",
]


doctype_list_js = {
    "NP Sender Profile": "public/js/np_sender_profile_list.js",
    "UP Sender Profile": "public/js/up_sender_profile_list.js",
}

permission_query_conditions = {
    "UA Integration Operation": "ukrainian_integrations.utils.operations.get_permission_query_conditions",
    "VitalPBX Call Log": "ukrainian_integrations.pbx_sms.vitalpbx.events.get_permission_query_conditions",
}

has_permission = {
    "UA Integration Operation": "ukrainian_integrations.utils.operations.has_permission",
    "VitalPBX Call Log": "ukrainian_integrations.pbx_sms.vitalpbx.events.has_permission",
}
