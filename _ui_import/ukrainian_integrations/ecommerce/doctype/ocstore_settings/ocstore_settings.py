from __future__ import annotations

import re
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document

from ukrainian_integrations.ecommerce.base.transport import FileDeliveryTransport
from ukrainian_integrations.utils.security import SALES_MANAGER_ROLES, require_roles
from ukrainian_integrations.utils.validation import validate_http_url

_FILE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SUPPORTED_ENTITIES = {"Products", "Prices", "Stock", "Photos", "Orders"}


class OcStoreSettings(Document):
    """One independently configured ocStore instance.

    This is intentionally a normal multi-record DocType. Its stable channel key
    is ``OcStore Settings:<document name>``.
    """

    def before_validate(self):
        if self.is_new() and not self.get("sync_entities"):
            for entity, layout in (
                ("Products", "OcStore Products XML v1"),
                ("Prices", "OcStore Prices XML v1"),
                ("Stock", "OcStore Stock XML v1"),
                ("Photos", "OcStore Photos XML v1"),
                ("Orders", "OcStore Orders XML v1"),
            ):
                if frappe.db.exists("Ecommerce File Layout", layout):
                    self.append(
                        "sync_entities",
                        {
                            "entity": entity,
                            "enabled": 0,
                            "method": "File",
                            "interval_minutes": 60 if entity == "Orders" else 1440,
                            "file_format": "XML",
                            "file_layout": layout,
                        },
                    )

    def validate(self):
        self.store_name = (self.store_name or "").strip()
        self.currency = (self.currency or "UAH").strip().upper()
        self.export_file_prefix = _file_token(self.export_file_prefix, "Export File Prefix")
        self.orders_file_prefix = _file_token(self.orders_file_prefix, "Orders File Prefix")
        self.max_order_files_per_run = _bounded_int(self.max_order_files_per_run, 1, 200, 20)
        self._validate_store_url()
        self._validate_sync_entities()
        self._validate_warehouses()
        self._validate_payment_routes()
        self._validate_order_statuses()
        self._validate_required_transports()
        self._validate_price_list()

    def _validate_store_url(self):
        if not self.store_url:
            return
        # Disabled migration copies may preserve an old HTTP storefront URL,
        # but an enabled production channel must use HTTPS.
        validate_http_url(self.store_url, "ocStore URL", allow_http=not int(self.enabled or 0))
        parsed = urlparse(self.store_url)
        self.store_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"

    def _validate_sync_entities(self):
        rows = list(self.get("sync_entities") or [])
        entities = [(row.get("entity") or "").strip() for row in rows]
        if len(entities) != len(set(entities)):
            frappe.throw(_("Synchronization entities must be unique within an ocStore instance"))
        for row in rows:
            row.run_method("validate")
            if row.entity not in _SUPPORTED_ENTITIES:
                frappe.throw(_("ocStore does not support the {0} synchronization entity").format(row.entity))
            if row.method not in {"File", "Disabled"}:
                frappe.throw(_("ocStore supports File synchronization only"))
            if int(row.enabled or 0) and (row.method != "File" or row.file_format != "XML"):
                frappe.throw(_("Enabled ocStore entities require File / XML synchronization"))

    def _validate_warehouses(self):
        rows = list(self.get("warehouses") or [])
        values = [(row.get("warehouse") or "").strip() for row in rows]
        if len(values) != len(set(values)):
            frappe.throw(_("Warehouses must be unique within an ocStore instance"))
        for row in rows:
            row.run_method("validate")
            company = frappe.db.get_value("Warehouse", row.warehouse, "company")
            if (
                company
                and company != self.company
                and (int(self.enabled or 0) or self._enabled(_SUPPORTED_ENTITIES))
            ):
                frappe.throw(_("Warehouse {0} belongs to another company").format(row.warehouse))
        if self._enabled({"Products", "Prices", "Stock", "Photos"}) and not any(
            int(row.enabled or 0) for row in rows
        ):
            frappe.throw(_("An enabled ocStore export requires at least one enabled warehouse"))

    def _validate_payment_routes(self):
        rows = list(self.get("payment_routes") or [])
        values = [(row.get("channel_payment_type") or "").strip() for row in rows]
        if len(values) != len(set(values)):
            frappe.throw(_("Payment routes must be unique within an ocStore instance"))
        for row in rows:
            row.run_method("validate")

    def _validate_order_statuses(self):
        rows = list(self.get("order_status_map") or [])
        values = [(row.get("channel_status") or "").strip() for row in rows]
        if len(values) != len(set(values)):
            frappe.throw(_("Order statuses must be unique within an ocStore instance"))
        for row in rows:
            row.run_method("validate")
        if self._enabled({"Orders"}) and not rows:
            frappe.throw(_("Enabled ocStore order import requires at least one status mapping"))

    def _validate_required_transports(self):
        if self._enabled(_SUPPORTED_ENTITIES) and not self.ftp_profile:
            frappe.throw(_("Enabled ocStore file synchronization requires an Exchange FTP Profile"))
        if self._enabled({"Photos"}):
            if not self.photo_ftp_profile or not self.photo_url_prefix:
                frappe.throw(_("Enabled ocStore photo export requires a Photo FTP Profile and URL Prefix"))
            validate_http_url(self.photo_url_prefix, "Photo URL Prefix")
            self.photo_url_prefix = self.photo_url_prefix.rstrip("/") + "/"

    def _validate_price_list(self):
        row = frappe.db.get_value(
            "Price List",
            self.selling_price_list,
            ["enabled", "selling", "currency"],
            as_dict=True,
        )
        if not row:
            frappe.throw(_("ocStore Price List does not exist"))
        active = int(self.enabled or 0) or self._enabled(_SUPPORTED_ENTITIES)
        if active and (not int(row.enabled or 0) or not int(row.selling or 0)):
            frappe.throw(_("ocStore requires an enabled selling Price List"))
        if active and row.currency and str(row.currency).upper() != self.currency:
            frappe.throw(_("ocStore Price List currency must match the channel currency"))

    def _enabled(self, entities: set[str]) -> bool:
        return any(
            row.entity in entities and int(row.enabled or 0) and row.method == "File"
            for row in (self.get("sync_entities") or [])
        )

    @frappe.whitelist(methods=["POST"])
    def test_connections(self):
        require_roles(*SALES_MANAGER_ROLES)
        self.check_permission("write")
        result = {}
        if self.ftp_profile:
            result["exchange"] = FileDeliveryTransport(
                frappe.get_doc("File Delivery Endpoint", self.ftp_profile)
            ).test_connection()
        if self.photo_ftp_profile:
            result["photos"] = FileDeliveryTransport(
                frappe.get_doc("File Delivery Endpoint", self.photo_ftp_profile)
            ).test_connection()
        if not result:
            frappe.throw(_("Configure at least one File Delivery Endpoint"))
        return {"ok": True, "connections": result}

    @frappe.whitelist(methods=["POST"])
    def export_now(self, force: int = 1):
        require_roles(*SALES_MANAGER_ROLES)
        self.check_permission("write")
        from ukrainian_integrations.ecommerce.providers.ocstore.service import export_bundle

        return export_bundle(self.name, force=bool(int(force or 0)))

    @frappe.whitelist(methods=["POST"])
    def import_orders_now(self):
        require_roles(*SALES_MANAGER_ROLES)
        self.check_permission("write")
        from ukrainian_integrations.ecommerce.providers.ocstore.service import import_order_files

        return import_order_files(self.name)


def _file_token(value: str, label: str) -> str:
    parsed = str(value or "").strip()
    if not _FILE_TOKEN.fullmatch(parsed):
        frappe.throw(_("{0} must contain only letters, digits, dot, dash or underscore").format(label))
    return parsed


def _bounded_int(value, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))
