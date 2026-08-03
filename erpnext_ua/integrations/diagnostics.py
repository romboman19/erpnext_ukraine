from __future__ import annotations

import frappe

REQUIRED_DOCTYPES = (
    "Customer Birthday Greeting Log",
    "Customer Identification Request",
    "Customer Identification Settings",
    "Ecommerce Channel",
    "Ecommerce Customer Mapping",
    "Ecommerce File Field",
    "Ecommerce File Exchange",
    "Ecommerce File Layout",
    "Ecommerce Item Mapping",
    "Ecommerce Order Status Map",
    "Ecommerce Payment Route",
    "Ecommerce Status Mapping",
    "Ecommerce Sync Entity Config",
    "Ecommerce Sync Log",
    "Ecommerce Warehouse Sync",
    "Ecommerce Warehouse Mapping",
    "File Delivery Endpoint",
    "Hunter Integration Log",
    "LiqPay Profile",
    "LiqPay Settings",
    "Monobank Profile",
    "Monobank Settings",
    "NP Sender Branch Row",
    "NP Sender Profile",
    "OcStore Settings",
    "PrivatBank Profile",
    "PrivatBank Settings",
    "RZ Delivery Sender Profile",
    "TurboSMS Settings",
    "TurboSMS Sender",
    "TurboSMS Log",
    "Telegram Bot Profile",
    "UA Integration Operation",
    "UP Sender Profile",
    "VitalPBX Call Log",
    "VitalPBX Settings",
)

REQUIRED_CUSTOM_FIELDS = {
    "Bank Transaction": ("ua_integration_key",),
    "Customer": ("ua_external_customer_key", "ua_telegram_chat_id"),
    "Communication": ("ua_telegram_bot_profile",),
    "Employee": ("ua_telegram_chat_id",),
    "Notification": ("ua_telegram_bot_profile", "ua_telegram_disable_web_page_preview"),
    "Notification Recipient": ("ua_telegram_chat_id",),
    "Sales Invoice": (
        "liqpay_status",
        "liqpay_transaction_id",
        "liqpay_paid_amount",
        "np_ttn_number",
        "np_ttn_ref",
        "np_status",
        "np_sender_profile",
        "np_last_sync_at",
        "rz_track_id",
        "rz_status_code",
        "rz_status",
        "rz_sender_profile",
        "rz_last_sync_at",
        "rz_shipping_cost",
        "rz_estimated_delivery_date",
        "rz_payment_fee",
        "up_barcode",
        "up_shipment_id",
        "up_status",
        "up_sender_profile",
        "up_last_sync_at",
        "ua_external_order_key",
        "ua_ecommerce_channel",
        "ua_external_order_id",
        "ua_ecommerce_status",
    ),
    "Sales Order": (
        "ua_external_order_key",
        "ua_ecommerce_channel",
        "ua_external_order_id",
        "ua_ecommerce_status",
        "ua_ecommerce_reserve_until",
    ),
    "Supplier": ("ua_telegram_chat_id",),
    "User": ("vitalpbx_extension", "ua_telegram_chat_id"),
}

SECRET_FIELDS = {
    "Customer Identification Settings": (
        "telegram_webhook_secret",
    ),
    "Ecommerce Channel": ("api_password",),
    "File Delivery Endpoint": ("password", "ssh_key"),
    "LiqPay Profile": ("private_key",),
    "Monobank Profile": ("token",),
    "NP Sender Profile": ("api_key",),
    "PrivatBank Profile": ("token",),
    "RZ Delivery Sender Profile": ("api_token",),
    "TurboSMS Settings": ("token",),
    "Telegram Bot Profile": ("bot_token",),
    "UP Sender Profile": ("ecom_token", "tracking_token", "counterparty_token"),
    "VitalPBX Settings": ("api_key", "webhook_key"),
}


def run_installation_checks(*, raise_on_error: bool = True) -> dict:
    """Validate the installed schema without making external API calls."""
    errors: list[str] = []

    installed_apps = set(frappe.get_installed_apps())
    for app in ("erpnext", "erpnext_ua"):
        if app not in installed_apps:
            errors.append(f"Required app is not installed: {app}")

    for doctype in REQUIRED_DOCTYPES:
        if not frappe.db.exists("DocType", doctype):
            errors.append(f"Missing DocType: {doctype}")

    for doctype, fields in REQUIRED_CUSTOM_FIELDS.items():
        meta = frappe.get_meta(doctype)
        for fieldname in fields:
            if not meta.has_field(fieldname):
                errors.append(f"Missing field: {doctype}.{fieldname}")

    for doctype, fields in SECRET_FIELDS.items():
        meta = frappe.get_meta(doctype)
        for fieldname in fields:
            field = meta.get_field(fieldname)
            if not field or field.fieldtype != "Password":
                errors.append(f"Secret must use Password fieldtype: {doctype}.{fieldname}")

    for doctype, fieldname in (
        ("Bank Transaction", "ua_integration_key"),
        ("Customer", "ua_external_customer_key"),
        ("Customer Birthday Greeting Log", "unique_key"),
        ("Customer Identification Request", "request_token"),
        ("Sales Order", "ua_external_order_key"),
        ("Sales Invoice", "ua_external_order_key"),
        ("Sales Invoice", "rz_track_id"),
        ("UA Integration Operation", "idempotency_key"),
        ("VitalPBX Call Log", "call_id"),
    ):
        field = frappe.get_meta(doctype).get_field(fieldname)
        if not field or not field.unique:
            errors.append(f"Idempotency field must be unique: {doctype}.{fieldname}")

    result = {
        "ok": not errors,
        "errors": errors,
        "installed_apps": sorted(installed_apps.intersection({"frappe", "erpnext", "erpnext_ua"})),
        "required_doctypes": len(REQUIRED_DOCTYPES),
    }
    if errors and raise_on_error:
        raise RuntimeError("Installation diagnostics failed: " + "; ".join(errors))
    return result
