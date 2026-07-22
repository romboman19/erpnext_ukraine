from __future__ import annotations

import frappe

from ukrainian_integrations.ecommerce.core.registry import get_providers
from ukrainian_integrations.utils.logger import log_event


def sync_orders_all() -> dict:
    out = {"ok": True, "providers": {}}
    for p in get_providers():
        try:
            if not p.is_enabled():
                out["providers"][p.code] = {"ok": True, "skipped": True, "reason": "disabled"}
                continue
            r = p.sync_orders()
            out["providers"][p.code] = r
            if isinstance(r, dict) and r.get("ok") is False:
                out["ok"] = False
        except Exception:
            out["providers"][p.code] = {"ok": False, "error": "exception"}
            out["ok"] = False
            log_event("ecommerce", "error", f"sync_orders failed for {p.code}", error_trace=frappe.get_traceback())

    log_event("ecommerce", "success" if out["ok"] else "error", "sync_orders_all finished", response_payload=out)
    return out


def sync_stock_all() -> dict:
    out = {"ok": True, "providers": {}}
    for p in get_providers():
        try:
            if not p.is_enabled():
                out["providers"][p.code] = {"ok": True, "skipped": True, "reason": "disabled"}
                continue
            r = p.sync_stock()
            out["providers"][p.code] = r
            if isinstance(r, dict) and r.get("ok") is False:
                out["ok"] = False
        except Exception:
            out["providers"][p.code] = {"ok": False, "error": "exception"}
            out["ok"] = False
            log_event("ecommerce", "error", f"sync_stock failed for {p.code}", error_trace=frappe.get_traceback())

    log_event("ecommerce", "success" if out["ok"] else "error", "sync_stock_all finished", response_payload=out)
    return out


def sync_customers_all() -> dict:
    return _sync_all("sync_customers", "sync_customers_all")


def sync_catalog_all() -> dict:
    return _sync_all("sync_catalog", "sync_catalog_all")


def sync_order_statuses_all() -> dict:
    return _sync_all("sync_order_statuses", "sync_order_statuses_all")


def _sync_all(method_name: str, event_name: str) -> dict:
    out = {"ok": True, "providers": {}}
    for provider in get_providers():
        try:
            if not provider.is_enabled():
                out["providers"][provider.code] = {"ok": True, "skipped": True, "reason": "disabled"}
                continue
            result = getattr(provider, method_name)()
            out["providers"][provider.code] = result
            if isinstance(result, dict) and result.get("ok") is False:
                out["ok"] = False
        except Exception:
            out["providers"][provider.code] = {"ok": False, "error": "exception"}
            out["ok"] = False
            log_event(
                "ecommerce",
                "error",
                f"{method_name} failed for {provider.code}",
                error_trace=frappe.get_traceback(),
            )
    log_event(
        "ecommerce",
        "success" if out["ok"] else "error",
        f"{event_name} finished",
        response_payload=out,
    )
    return out
