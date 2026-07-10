app_name = "erpnext_ua"
app_title = "ERPNext Ukraine"
app_publisher = "HUNTER.rv"
app_description = "Ukrainian localization for ERPNext: FOP profiles, tax parameters, print formats, translations"
app_email = "it@hunter.rv.ua"
app_license = "MIT"
required_apps = ["erpnext"]

after_migrate = [
    "erpnext_ua.install.ensure_tax_parameters",
]
