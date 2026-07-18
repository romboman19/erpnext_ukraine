from __future__ import annotations

from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document

from ukrainian_integrations.utils.validation import validate_allowed_host, validate_http_url

API_TRANSPORT_FIELDS = (
    "catalog_transport",
    "stock_transport",
    "orders_transport",
    "customers_transport",
    "order_status_transport",
)


class EcommerceChannel(Document):
    def validate(self):
        self.channel_name = (self.channel_name or "").strip()
        self.currency = (self.currency or "UAH").strip().upper()
        self.api_batch_size = _bounded_int(self.api_batch_size, 1, 500, 200)
        self.orders_page_size = _bounded_int(self.orders_page_size, 1, 500, 100)
        self.orders_max_pages = _bounded_int(self.orders_max_pages, 1, 200, 50)
        self.orders_overlap_minutes = _bounded_int(self.orders_overlap_minutes, 0, 1440, 15)
        self.initial_sync_days = _bounded_int(self.initial_sync_days, 1, 365, 7)
        self._validate_routes()
        self._validate_warehouses()
        self._validate_status_mappings()
        self._validate_api()

    def _validate_routes(self):
        allowed = {"Disabled", "API", "XML"}
        for fieldname in API_TRANSPORT_FIELDS:
            value = self.get(fieldname) or "Disabled"
            if value not in allowed:
                frappe.throw(_("Unsupported synchronization transport: {0}").format(value))

        if self.provider == "ocStore":
            api_routes = [fieldname for fieldname in API_TRANSPORT_FIELDS if self.get(fieldname) == "API"]
            if api_routes:
                frappe.throw(_("ocStore channel supports file exchange only; select XML or Disabled"))
            self.catalog_xml_profile = "ERPNext Exchange XML v1"
            self.order_xml_profile = "ERPNext Exchange XML v1"
        elif self.provider == "Shop-Express" and not self.catalog_xml_profile:
            self.catalog_xml_profile = "Shop-Express YML"
        if self.catalog_transport == "XML" and self.catalog_xml_profile == "Shop-Express YML":
            if not self.store_url:
                frappe.throw(_("Store URL is required for the Shop-Express YML catalog"))
            validate_http_url(self.store_url, "Store URL")

    def _validate_warehouses(self):
        rows = list(self.get("warehouses") or [])
        if not rows:
            frappe.throw(_("At least one ERPNext warehouse is required"))
        values = [(row.get("warehouse") or "").strip() for row in rows]
        if len(values) != len(set(values)):
            frappe.throw(_("Warehouse mappings must be unique within a channel"))
        for row in rows:
            if float(row.get("safety_stock") or 0) < 0:
                frappe.throw(_("Safety stock cannot be negative"))

    def _validate_status_mappings(self):
        rows = list(self.get("status_mappings") or [])
        external_ids = [(row.get("external_status_id") or "").strip() for row in rows]
        if len(external_ids) != len(set(external_ids)):
            frappe.throw(_("External status IDs must be unique within a channel"))
        pushed_statuses = [
            (row.get("erpnext_status") or "").strip()
            for row in rows
            if int(row.get("push_to_channel") or 0)
        ]
        if any(not status for status in pushed_statuses):
            frappe.throw(_("Each pushed status mapping requires an ERPNext status"))
        if len(pushed_statuses) != len(set(pushed_statuses)):
            frappe.throw(_("Only one pushed external status is allowed per ERPNext status"))

    def _validate_api(self):
        if not any(self.get(fieldname) == "API" for fieldname in API_TRANSPORT_FIELDS):
            return
        if self.provider != "Shop-Express":
            frappe.throw(_("API transport is not implemented for this provider"))
        if not self.api_base_url:
            frappe.throw(_("API Base URL is required for API synchronization"))
        validate_http_url(self.api_base_url, "API Base URL")
        if int(self.enabled or 0) == 1:
            validate_allowed_host(
                self.api_base_url,
                "Shop-Express API Base URL",
                default_hosts=set(),
                config_key="shop_express_allowed_api_hosts",
            )
        parsed = urlparse(self.api_base_url)
        self.api_base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def _bounded_int(value, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))
