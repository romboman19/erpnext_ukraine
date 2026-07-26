from __future__ import annotations

import frappe

from erpnext_ua.ecommerce.providers.ocstore.provider import OcStoreProvider
from erpnext_ua.ecommerce.providers.prom_ua.provider import PromUAProvider
from erpnext_ua.ecommerce.providers.shop_express.provider import ShopExpressProvider

CHANNEL_PROVIDERS = {
    "Shop-Express": ShopExpressProvider,
    "ocStore": OcStoreProvider,
}


def get_providers():
    providers = [PromUAProvider()]
    if not frappe.db.exists("DocType", "Ecommerce Channel"):
        return providers
    for name in frappe.get_all("Ecommerce Channel", pluck="name", order_by="name asc"):
        channel = frappe.get_doc("Ecommerce Channel", name)
        provider_class = CHANNEL_PROVIDERS.get(channel.provider)
        if provider_class:
            providers.append(provider_class(channel))
    return providers


def get_provider_for_channel(channel_name: str, *, require_enabled: bool = True):
    channel = frappe.get_doc("Ecommerce Channel", channel_name)
    channel.check_permission("read")
    provider_class = CHANNEL_PROVIDERS.get(channel.provider)
    if not provider_class:
        raise ValueError(f"Unsupported ecommerce channel provider: {channel.provider}")
    provider = provider_class(channel)
    if require_enabled and not provider.is_enabled():
        raise ValueError("Ecommerce channel is disabled")
    return provider
