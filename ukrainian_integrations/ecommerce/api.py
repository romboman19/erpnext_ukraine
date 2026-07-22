from __future__ import annotations

import frappe

from ukrainian_integrations.ecommerce.core.file_exchange import generate_export, process_import
from ukrainian_integrations.ecommerce.core.registry import get_provider_for_channel
from ukrainian_integrations.utils.security import SALES_MANAGER_ROLES, require_roles


@frappe.whitelist(methods=["POST"])
def test_channel(channel: str) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    provider = get_provider_for_channel(channel, require_enabled=False)
    return provider.test_connection()


@frappe.whitelist(methods=["POST"])
def sync_channel_orders(channel: str) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    provider = get_provider_for_channel(channel)
    return provider.sync_orders()


@frappe.whitelist(methods=["POST"])
def sync_channel_stock(channel: str) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    provider = get_provider_for_channel(channel)
    return provider.sync_stock()


@frappe.whitelist(methods=["POST"])
def sync_channel_customers(channel: str) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    provider = get_provider_for_channel(channel)
    return provider.sync_customers()


@frappe.whitelist(methods=["POST"])
def sync_channel_catalog(channel: str) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    provider = get_provider_for_channel(channel)
    return provider.sync_catalog()


@frappe.whitelist(methods=["POST"])
def sync_channel_order_statuses(channel: str) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    provider = get_provider_for_channel(channel)
    return provider.sync_order_statuses()


@frappe.whitelist(methods=["POST"])
def generate_catalog_file(channel: str) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    doc = frappe.get_doc("Ecommerce Channel", channel)
    doc.check_permission("read")
    return generate_export(doc, "Catalog")


@frappe.whitelist(methods=["POST"])
def generate_stock_file(channel: str) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    doc = frappe.get_doc("Ecommerce Channel", channel)
    doc.check_permission("read")
    return generate_export(doc, "Prices and Stock")


@frappe.whitelist(methods=["POST"])
def process_exchange_file(exchange: str) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    doc = frappe.get_doc("Ecommerce File Exchange", exchange)
    doc.check_permission("write")
    return process_import(doc)
