app_name = "erpnext_ua"
app_title = "ERPNext Україна"
app_publisher = "HUNTER.rv"
app_description = "Український бізнес-модуль ERPNext: ФОП, каса, ПРРО, облік, документи й цінники"
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

before_migrate = [
	"erpnext_ua.install.ensure_app_modules",
	"erpnext_ua.install.ensure_pos_workspace",
]

after_install = [
	"erpnext_ua.install.ensure_app_modules",
	"erpnext_ua.install.ensure_accounting_setup",
	"erpnext_ua.install.ensure_pos_workspace",
    "erpnext_ua.install.ensure_tax_parameters",
    "erpnext_ua.install.ensure_pos_setup",
	"erpnext_ua.install.ensure_prro_setup",
	"erpnext_ua.install.ensure_pos_printers",
	"erpnext_ua.install.ensure_pos_page",
	"erpnext_ua.install.ensure_price_tag_setup",
]

after_migrate = [
	"erpnext_ua.install.ensure_app_modules",
	"erpnext_ua.install.ensure_accounting_setup",
	"erpnext_ua.install.ensure_pos_workspace",
    "erpnext_ua.install.ensure_tax_parameters",
    "erpnext_ua.install.ensure_pos_setup",
	"erpnext_ua.install.ensure_prro_setup",
	"erpnext_ua.install.ensure_pos_printers",
	"erpnext_ua.install.ensure_pos_page",
	"erpnext_ua.install.ensure_price_tag_setup",
]

doctype_js = {
	"Sales Invoice": "ua_fiscal/doctype_js/sales_invoice_fiscal.js",
	"PB POS Terminal": "ua_pos/public/js/pb_pos_terminal.js",
	"PRRO Receipt": "ua_fiscal/doctype_js/prro_receipt.js",
	"Purchase Receipt": "ua_price_tags/public/js/price_tag_source.js",
	"Stock Entry": "ua_price_tags/public/js/price_tag_source.js",
	"Delivery Note": "ua_price_tags/public/js/price_tag_source.js",
	"Item": "ua_price_tags/public/js/price_tag_source.js",
}

doc_events = {
    "Sales Invoice": {
        "on_submit": "erpnext_ua.ua_fiscal.sales_invoice.on_submit",
    },
}

scheduler_events = {
	"cron": {
		"*/5 * * * *": [
			"erpnext_ua.ua_fiscal.recovery.recover_fiscal_state",
		],
		"* * * * *": [
			"erpnext_ua.ua_pos.print_service.process_print_queue",
		],
	},
    "daily": [
        "erpnext_ua.ua_fop.tax_calendar.update_statuses_and_notify",
        "erpnext_ua.ua_fop.income_monitor.check_income_limits",
    ],
    "monthly": [
        "erpnext_ua.ua_fop.tax_calendar.generate_for_all_fops",
    ],
}
