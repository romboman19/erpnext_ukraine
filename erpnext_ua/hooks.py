app_name = "erpnext_ua"
app_title = "ERPNext Україна"
app_publisher = "HUNTER.rv"
app_description = (
    "Українська прикладна логіка ERPNext: ФОП, каса, ПРРО, облік, документи, цінники, "
    "комісійно-консигнаційна торгівля, доставка, банки, платежі, маркетплейси та комунікації"
)
app_email = "it@hunter.rv.ua"
app_license = "MIT"

stock_domain_providers = [
	"erpnext_ua.consignment_and_commission.providers.CCStockDomainProvider",
]
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
	"erpnext_ua.ua_fop.tax_calendar.generate_for_all_fops",
    "erpnext_ua.install.ensure_pos_setup",
	"erpnext_ua.install.ensure_prro_setup",
	"erpnext_ua.install.ensure_pos_printers",
	"erpnext_ua.install.ensure_pos_page",
	"erpnext_ua.install.ensure_price_tag_doctypes",
	"erpnext_ua.install.ensure_price_tag_setup",
	"erpnext_ua.install.ensure_item_spec_doctypes",
	"erpnext_ua.install.ensure_item_spec_setup",
	"erpnext_ua.print_designer_setup.ensure_print_designer_formats",
	"erpnext_ua.consignment_and_commission.setup.ownership_dimension.ensure_ownership_dimension",
	# GSF setup runs after the commission domain on purpose: ADR-001 requires the
	# order of domain provisioning to be fixed and explicit, not incidental.
	"erpnext_ua.group_stock_fifo.setup.roles.ensure_roles",
	"erpnext_ua.group_stock_fifo.setup.layer_dimension.ensure_layer_dimension",
	"erpnext_ua.group_stock_fifo.setup.cc_discovery.discover_cc_warehouses",
	"erpnext_ua.ua_loyalty.setup.ensure_loyalty_setup",
	"erpnext_ua.ua_gift_certificates.setup.ensure_gift_certificate_setup",
	"erpnext_ua.integrations.install.after_install",
	"erpnext_ua.ua_setup.service.report_readiness",
]

after_migrate = [
	"erpnext_ua.install.ensure_app_modules",
	"erpnext_ua.install.ensure_accounting_setup",
	"erpnext_ua.install.ensure_receiving_setup",
	"erpnext_ua.install.ensure_pos_workspace",
    "erpnext_ua.install.ensure_tax_parameters",
	"erpnext_ua.ua_fop.tax_calendar.generate_for_all_fops",
    "erpnext_ua.install.ensure_pos_setup",
	"erpnext_ua.install.ensure_prro_setup",
	"erpnext_ua.install.ensure_pos_printers",
	"erpnext_ua.install.ensure_pos_page",
	"erpnext_ua.install.ensure_price_tag_doctypes",
	"erpnext_ua.install.ensure_price_tag_setup",
	"erpnext_ua.install.ensure_item_spec_doctypes",
	"erpnext_ua.install.ensure_item_spec_setup",
	"erpnext_ua.print_designer_setup.ensure_print_designer_formats",
	"erpnext_ua.consignment_and_commission.setup.ownership_dimension.ensure_ownership_dimension",
	"erpnext_ua.consignment_and_commission.setup.financial_backfill.backfill_financial_snapshots",
	"erpnext_ua.group_stock_fifo.setup.roles.ensure_roles",
	# ADR-002: the cleanup patch must run in the same after_migrate, right after
	# ERPNext finishes registering the dimension's custom fields.
	"erpnext_ua.group_stock_fifo.setup.layer_dimension.ensure_layer_dimension",
	"erpnext_ua.group_stock_fifo.setup.cc_discovery.discover_cc_warehouses",
	"erpnext_ua.ua_loyalty.setup.ensure_loyalty_setup",
	"erpnext_ua.ua_gift_certificates.setup.ensure_gift_certificate_setup",
	"erpnext_ua.integrations.migrations.after_migrate",
]

before_uninstall = "erpnext_ua.integrations.uninstall.before_uninstall"

doctype_js = {
	"Sales Invoice": [
		"ua_fiscal/doctype_js/sales_invoice_fiscal.js",
		"public/js/sales_invoice_global_fifo.js",
		"public/js/sales_invoice_shipment_actions.js",
		"public/js/sales_invoice_vitalpbx_actions.js",
	],
	"Sales Order": "public/js/sales_order_global_fifo.js",
	"PB POS Terminal": "ua_pos/public/js/pb_pos_terminal.js",
	"PRRO Receipt": "ua_fiscal/doctype_js/prro_receipt.js",
	"Purchase Receipt": ["public/js/price_tag_source.js", "public/js/purchase_vat.js"],
	"Purchase Invoice": "public/js/purchase_vat.js",
	"Stock Entry": "public/js/price_tag_source.js",
	"Delivery Note": "public/js/price_tag_source.js",
	"Item": ["public/js/price_tag_source.js", "public/js/item_specifications.js"],
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
GSF = "erpnext_ua.group_stock_fifo"
LOYALTY = "erpnext_ua.ua_loyalty.adapters.sales_invoice"
GIFT_CERTIFICATES = "erpnext_ua.ua_gift_certificates.adapters.sales_invoice"

# Порядок у списках — це контракт, а не випадковість. Комісійні перевірки й
# споживання резервів мають завершитися до фіскалізації ПРРО: скасувати вже
# надісланий чек значно дорожче, ніж не створити його.
doc_events = {
    "Delivery Note": {
        "before_submit": f"{GSF}.services.delivery_note.validate_managed_warehouses",
    },
    "Sales Invoice": {
        "before_submit": [
            f"{CC}.sales_invoice.validate_managed_sales_invoice",
            f"{CC}.tracking.validate_sales_invoice_tracking_ownership",
            f"{GSF}.services.sales_invoice.validate_managed_sales_invoice",
			f"{LOYALTY}.validate_before_submit",
			f"{GIFT_CERTIFICATES}.validate_before_submit",
        ],
        "on_submit": [
            f"{CC}.sales_invoice.consume_sales_invoice_allocations",
			f"{LOYALTY}.on_submit",
			f"{GIFT_CERTIFICATES}.on_submit",
            "erpnext_ua.ua_fiscal.sales_invoice.on_submit",
        ],
        "before_cancel": [
            f"{CC}.sales_invoice.before_cancel_managed_sales_invoice",
            f"{GSF}.services.sales_invoice.before_cancel_managed_sales_invoice",
			f"{LOYALTY}.validate_before_cancel",
			f"{GIFT_CERTIFICATES}.validate_before_cancel",
		],
		"on_cancel": [f"{CC}.sales_invoice.on_cancel_managed_sales_invoice", f"{LOYALTY}.on_cancel", f"{GIFT_CERTIFICATES}.on_cancel"],
        "on_trash": f"{CC}.sales_invoice.release_draft_sales_invoice_allocations",
    },
	"Employee": {
		"before_validate": "erpnext_ua.ua_pos.employee_barcode.assign_employee_barcode",
	},
	"Item": {
		"validate": "erpnext_ua.ua_item_specs.item_hooks.validate_specifications",
	},
	"Item Group": {
		"validate": "erpnext_ua.ua_item_specs.item_hooks.validate_group_specifications",
		"on_update": "erpnext_ua.ua_item_specs.item_hooks.clear_specification_cache",
	},
	"Purchase Receipt": {
		"before_validate": "erpnext_ua.ua_receiving.pricing.apply_supplier_price_vat",
		"before_submit": [
			"erpnext_ua.ua_receiving.service.validate_purchase_receipt",
			f"{GSF}.services.period.guard_backdated_document",
			f"{GSF}.receipts.register_receipt_layers",
		],
		"on_submit": f"{GSF}.receipts.open_receipt_layers",
		"before_cancel": f"{GSF}.receipts.guard_receipt_cancellation",
		"on_cancel": f"{GSF}.receipts.reverse_receipt_layers",
	},
	"Purchase Invoice": {
		"before_validate": "erpnext_ua.ua_receiving.pricing.apply_supplier_price_vat",
		"before_submit": [
			f"{CC}.tracking.validate_purchase_invoice_tracking_ownership",
			f"{GSF}.services.period.guard_backdated_document",
			f"{GSF}.receipts.register_receipt_layers",
		],
		"on_submit": f"{GSF}.receipts.open_receipt_layers",
		"before_cancel": [
			f"{CC}.purchase_invoice.guard_linked_own_receipt_cancellation",
			f"{GSF}.receipts.guard_receipt_cancellation",
		],
		"on_cancel": [
			f"{CC}.purchase_invoice.allow_linked_own_receipt_cancellation",
			f"{GSF}.receipts.reverse_receipt_layers",
		],
	},
    # Інертні для звичайних складських операцій ERPNext: спрацьовують лише на
    # документах, явно пов'язаних із CC Receipt.
    "Stock Entry": {
        # §17.3: the guard runs before registration on purpose — an unmanaged
        # entry into a GSF pool is refused, never quietly given a layer.
        "before_submit": [
            f"{CC}.tracking.validate_stock_entry_tracking_ownership",
            f"{GSF}.receipts.guard_unmanaged_stock_document",
            f"{GSF}.receipts.register_receipt_layers",
        ],
        "on_submit": f"{GSF}.receipts.open_receipt_layers",
        "before_cancel": [
            f"{CC}.stock_entry.guard_linked_receipt_cancellation",
            f"{GSF}.receipts.guard_receipt_cancellation",
        ],
        "on_cancel": [
            f"{CC}.stock_entry.allow_linked_receipt_cancellation",
            f"{GSF}.receipts.reverse_receipt_layers",
        ],
    },
    "Stock Reconciliation": {
        # §11.1 admits reconciliation only through a separate controlled
        # scenario, which does not exist yet; until it does, refusal is correct.
        "before_submit": f"{GSF}.receipts.guard_unmanaged_stock_document",
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
        "on_submit": [
            f"{CC}.payments.update_settlement_after_payment",
            "erpnext_ua.ua_fiscal.ecommerce.on_payment_submit",
        ],
        "before_cancel": "erpnext_ua.ua_fiscal.ecommerce.before_payment_cancel",
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
		f"{GSF}.services.allocations.expire_due_allocations",
		"erpnext_ua.ua_loyalty.scheduler.activate_pending",
		"erpnext_ua.ua_loyalty.scheduler.expire_obligations",
		"erpnext_ua.ua_loyalty.scheduler.release_stale_reservations",
		"erpnext_ua.ua_gift_certificates.services.expiry.expire_due_certificates",
	],
	"cron": {
		"*/5 * * * *": [
			"erpnext_ua.ua_fiscal.recovery.recover_fiscal_state",
			"erpnext_ua.ua_fiscal.ecommerce.recover_pending_ecommerce_receipts",
		],
		"* * * * *": [
			"erpnext_ua.ua_fiscal.outbox.recover_due_jobs",
			"erpnext_ua.ua_pos.print_service.process_print_queue",
			"erpnext_ua.ua_gift_certificates.services.reservation.release_stale_reservations",
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
		"erpnext_ua.ua_gift_certificates.services.reconciliation.run_daily_reconciliation",
        "erpnext_ua.ua_fop.tax_calendar.update_statuses_and_notify",
        "erpnext_ua.ua_fop.income_monitor.check_income_limits",
        "erpnext_ua.integrations.customer_identification.birthday.send_scheduled_greetings",
        "erpnext_ua.integrations.utils.logger.purge_old_logs",
    ],
    "monthly": [
        "erpnext_ua.ua_fop.tax_calendar.generate_for_all_fops",
    ],
}
