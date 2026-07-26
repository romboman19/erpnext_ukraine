app_name = "erpnext_ua"
app_title = "ERPNext Україна"
app_publisher = "HUNTER.rv"
app_description = (
    "Українська прикладна логіка ERPNext: ФОП, каса, ПРРО, облік, документи, цінники, "
    "комісійно-консигнаційна торгівля, доставка, банки, платежі, маркетплейси та комунікації"
)
app_email = "it@hunter.rv.ua"
app_license = "MIT"
required_apps = ["erpnext"]
app_logo_url = "/assets/erpnext_ua/images/app-logo.svg"
app_home = "/app/ua-fop"

add_to_apps_screen = [
    {
        "name": app_name,
        "logo": app_logo_url,
        "title": app_title,
        "route": app_home,
    }
]

app_include_js = [
    "/assets/erpnext_ua/js/vitalpbx_popup_listener.js",
    "/assets/erpnext_ua/js/notification_realtime_listener.js",
]

fixtures = [
    {
        "dt": "Role",
        "filters": [
            [
                "name",
                "in",
                [
                    "Commission Trade Manager",
                    "Commission Trade User",
                    "Commission Trade Auditor",
                ],
            ]
        ],
    }
]

before_migrate = [
	"erpnext_ua.install.ensure_app_modules",
	"erpnext_ua.install.ensure_pos_workspace",
]

after_install = [
	"erpnext_ua.install.ensure_app_modules",
	"erpnext_ua.install.ensure_accounting_setup",
	"erpnext_ua.install.ensure_receiving_setup",
	"erpnext_ua.install.ensure_pos_workspace",
    "erpnext_ua.install.ensure_tax_parameters",
    "erpnext_ua.install.ensure_pos_setup",
	"erpnext_ua.install.ensure_prro_setup",
	"erpnext_ua.install.ensure_pos_printers",
	"erpnext_ua.install.ensure_pos_page",
	"erpnext_ua.install.ensure_price_tag_doctypes",
	"erpnext_ua.install.ensure_price_tag_setup",
	"erpnext_ua.print_designer_setup.ensure_print_designer_formats",
	"erpnext_ua.consignment_and_commission.setup.ownership_dimension.ensure_ownership_dimension",
	"erpnext_ua.integrations.install.after_install",
	"erpnext_ua.ua_setup.service.report_readiness",
]

after_migrate = [
	"erpnext_ua.install.ensure_app_modules",
	"erpnext_ua.install.ensure_accounting_setup",
	"erpnext_ua.install.ensure_receiving_setup",
	"erpnext_ua.install.ensure_pos_workspace",
    "erpnext_ua.install.ensure_tax_parameters",
    "erpnext_ua.install.ensure_pos_setup",
	"erpnext_ua.install.ensure_prro_setup",
	"erpnext_ua.install.ensure_pos_printers",
	"erpnext_ua.install.ensure_pos_page",
	"erpnext_ua.install.ensure_price_tag_doctypes",
	"erpnext_ua.install.ensure_price_tag_setup",
	"erpnext_ua.print_designer_setup.ensure_print_designer_formats",
	"erpnext_ua.consignment_and_commission.setup.ownership_dimension.ensure_ownership_dimension",
	"erpnext_ua.consignment_and_commission.setup.financial_backfill.backfill_financial_snapshots",
	"erpnext_ua.integrations.migrations.after_migrate",
]

before_uninstall = "erpnext_ua.integrations.uninstall.before_uninstall"

doctype_js = {
	"Sales Invoice": [
		"ua_fiscal/doctype_js/sales_invoice_fiscal.js",
		"public/js/sales_invoice_shipment_actions.js",
		"public/js/sales_invoice_vitalpbx_actions.js",
	],
	"PB POS Terminal": "ua_pos/public/js/pb_pos_terminal.js",
	"PRRO Receipt": "ua_fiscal/doctype_js/prro_receipt.js",
	"Purchase Receipt": ["public/js/price_tag_source.js", "public/js/purchase_vat.js"],
	"Purchase Invoice": "public/js/purchase_vat.js",
	"Stock Entry": "public/js/price_tag_source.js",
	"Delivery Note": "public/js/price_tag_source.js",
	"Item": "public/js/price_tag_source.js",
	"Customer": "public/js/customer_vitalpbx_actions.js",
	"Notification": "public/js/notification_telegram.js",
	"Notification Settings": "public/js/notification_settings_browser.js",
	"NP Sender Profile": "public/js/np_sender_profile_actions.js",
	"UP Sender Profile": "public/js/up_sender_profile_actions.js",
	"RZ Delivery Sender Profile": "public/js/rz_delivery_sender_profile_actions.js",
	"TurboSMS Settings": "public/js/turbosms_settings_actions.js",
	"Monobank Settings": "public/js/monobank_settings_actions.js",
	"PrivatBank Settings": "public/js/privatbank_settings_actions.js",
	"LiqPay Settings": "public/js/liqpay_settings_actions.js",
	"Telegram Bot Profile": "public/js/telegram_bot_profile.js",
}

doctype_list_js = {
	"NP Sender Profile": "public/js/np_sender_profile_list.js",
	"UP Sender Profile": "public/js/up_sender_profile_list.js",
}

extend_doctype_class = {
	"Notification": [
		"erpnext_ua.integrations.communication.telegram.notification.TelegramNotificationMixin",
	],
}

override_doctype_class = {
	"System Health Report": (
		"erpnext_ua.integrations.monitoring.system_health.ContainerAwareSystemHealthReport"
	),
}

permission_query_conditions = {
	"UA Integration Operation": "erpnext_ua.integrations.utils.operations.get_permission_query_conditions",
	"VitalPBX Call Log": "erpnext_ua.integrations.pbx_sms.vitalpbx.events.get_permission_query_conditions",
	"Customer Telegram Link": "erpnext_ua.integrations.customer_identification.telegram_link.get_permission_query_conditions",
}

has_permission = {
	"UA Integration Operation": "erpnext_ua.integrations.utils.operations.has_permission",
	"VitalPBX Call Log": "erpnext_ua.integrations.pbx_sms.vitalpbx.events.has_permission",
}

CC = "erpnext_ua.consignment_and_commission.integrations"

# Порядок у списках — це контракт, а не випадковість. Комісійні перевірки й
# споживання резервів мають завершитися до фіскалізації ПРРО: скасувати вже
# надісланий чек значно дорожче, ніж не створити його.
doc_events = {
    "Sales Invoice": {
        "before_submit": [
            f"{CC}.sales_invoice.validate_managed_sales_invoice",
            f"{CC}.tracking.validate_sales_invoice_tracking_ownership",
        ],
        "on_submit": [
            f"{CC}.sales_invoice.consume_sales_invoice_allocations",
            "erpnext_ua.ua_fiscal.sales_invoice.on_submit",
        ],
        "before_cancel": f"{CC}.sales_invoice.before_cancel_managed_sales_invoice",
        "on_cancel": f"{CC}.sales_invoice.on_cancel_managed_sales_invoice",
        "on_trash": f"{CC}.sales_invoice.release_draft_sales_invoice_allocations",
    },
	"Purchase Receipt": {
		"before_validate": "erpnext_ua.ua_receiving.pricing.apply_supplier_price_vat",
		"before_submit": "erpnext_ua.ua_receiving.service.validate_purchase_receipt",
	},
	"Purchase Invoice": {
		"before_validate": "erpnext_ua.ua_receiving.pricing.apply_supplier_price_vat",
		"before_submit": f"{CC}.tracking.validate_purchase_invoice_tracking_ownership",
		"before_cancel": f"{CC}.purchase_invoice.guard_linked_own_receipt_cancellation",
		"on_cancel": f"{CC}.purchase_invoice.allow_linked_own_receipt_cancellation",
	},
    # Інертні для звичайних складських операцій ERPNext: спрацьовують лише на
    # документах, явно пов'язаних із CC Receipt.
    "Stock Entry": {
        "before_submit": f"{CC}.tracking.validate_stock_entry_tracking_ownership",
        "before_cancel": f"{CC}.stock_entry.guard_linked_receipt_cancellation",
        "on_cancel": f"{CC}.stock_entry.allow_linked_receipt_cancellation",
    },
    "Journal Entry": {
        "before_cancel": [
            f"{CC}.sale_allocations.guard_recognition_cancellation",
            f"{CC}.settlements.guard_settlement_debt_cancellation",
            f"{CC}.settlement_adjustments.guard_adjustment_journal_cancellation",
        ]
    },
    "Payment Entry": {
        "validate": f"{CC}.payments.validate_settlement_payment",
        "on_submit": f"{CC}.payments.update_settlement_after_payment",
        "on_cancel": f"{CC}.payments.update_settlement_after_payment",
    },
    "Batch": {
        "validate": f"{CC}.tracking.validate_tracking_owner_immutability",
        "on_trash": f"{CC}.tracking.guard_owned_tracking_deletion",
    },
    "Serial No": {
        "validate": f"{CC}.tracking.validate_tracking_owner_immutability",
        "on_trash": f"{CC}.tracking.guard_owned_tracking_deletion",
    },
    "Customer": {
        "after_insert": "erpnext_ua.integrations.customer_identification.telegram_link.on_customer_insert",
    },
}

scheduler_events = {
	"all": [
		f"{CC}.pos.process_pending_print_jobs",
	],
	"hourly_long": [
		f"{CC}.reservations.expire_due_allocations",
	],
	"cron": {
		"*/5 * * * *": [
			"erpnext_ua.ua_fiscal.recovery.recover_fiscal_state",
		],
		"* * * * *": [
			"erpnext_ua.ua_pos.print_service.process_print_queue",
			"erpnext_ua.integrations.monitoring.system_health.update_scheduler_heartbeat",
			"erpnext_ua.ecommerce.scheduler.dispatch",
		],
		"*/10 * * * *": [
			"erpnext_ua.ecommerce.providers.prom_ua.service.pull_orders",
			"erpnext_ua.integrations.customer_identification.service.expire_pending",
		],
		"*/20 * * * *": [
			"erpnext_ua.ecommerce.providers.prom_ua.service.push_stock",
		],
		"*/30 * * * *": [
			"erpnext_ua.integrations.shipment.nova_poshta.scheduler.sync_ttn_statuses",
			"erpnext_ua.integrations.shipment.ukr_poshta.scheduler.sync_ttn_statuses",
			"erpnext_ua.integrations.shipment.rozetka_delivery.scheduler.sync_track_statuses",
			"erpnext_ua.integrations.payments.core.bank_import_scheduler.run_all_bank_imports",
		],
	},
    "daily": [
        "erpnext_ua.ua_fop.tax_calendar.update_statuses_and_notify",
        "erpnext_ua.ua_fop.income_monitor.check_income_limits",
        "erpnext_ua.integrations.customer_identification.birthday.send_scheduled_greetings",
        "erpnext_ua.integrations.utils.logger.purge_old_logs",
    ],
    "monthly": [
        "erpnext_ua.ua_fop.tax_calendar.generate_for_all_fops",
    ],
}
