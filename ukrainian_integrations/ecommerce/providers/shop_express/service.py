from __future__ import annotations

from datetime import timedelta

import frappe

from ukrainian_integrations.ecommerce.core.catalog import get_catalog
from ukrainian_integrations.ecommerce.core.orders import import_customers, import_orders
from ukrainian_integrations.ecommerce.providers.shop_express.api import ShopExpressClient
from ukrainian_integrations.utils.logger import log_event
from ukrainian_integrations.utils.validation import validate_allowed_host, validate_http_url


def client_for(channel) -> ShopExpressClient:
    base_url = (channel.api_base_url or "").strip()
    validate_http_url(base_url, "Shop-Express API Base URL")
    validate_allowed_host(
        base_url,
        "Shop-Express API Base URL",
        default_hosts=set(),
        config_key="shop_express_allowed_api_hosts",
    )
    password = channel.get_password("api_password")
    cache_key = f"shop_express_api_token:{frappe.local.site}:{channel.name}"
    cached_token = frappe.cache.get_value(cache_key) or ""

    def cache_token(token: str):
        frappe.cache.set_value(cache_key, token, expires_in_sec=540)

    return ShopExpressClient(
        base_url=base_url,
        login=channel.api_login,
        password=password,
        token=cached_token,
        token_callback=cache_token,
    )


def test_connection(channel) -> dict:
    client = client_for(channel)
    client.authenticate()
    statuses = client.export_statuses()
    rows = (statuses.get("response") or {}).get("statuses") or []
    return {"ok": True, "provider": "Shop-Express", "statuses": len(rows) if isinstance(rows, list) else 0}


def pull_orders(channel) -> dict:
    now = frappe.utils.now_datetime()
    start = _sync_start(
        channel.last_orders_sync_at,
        now,
        channel.orders_overlap_minutes,
        first_days=channel.initial_sync_days or 7,
    )
    page_size = max(1, min(int(channel.orders_page_size or 100), 500))
    max_pages = max(1, min(int(channel.orders_max_pages or 50), 200))
    api = client_for(channel)
    orders = []
    for page in range(max_pages):
        data = api.export_orders(
            **{
                "from": start.strftime("%Y.%m.%d %H:%M:%S"),
                "to": now.strftime("%Y.%m.%d %H:%M:%S"),
                "limit": page_size,
                "offset": page * page_size,
                "additional_data": 1,
            }
        )
        rows = (data.get("response") or {}).get("orders") or []
        if not isinstance(rows, list):
            raise ValueError("Shop-Express orders response must contain a list")
        orders.extend(_normalize_order(row) for row in rows)
        if len(rows) < page_size:
            break
    else:
        raise RuntimeError("Shop-Express order pagination exceeded the configured maximum")

    result = import_orders(channel, orders)
    if result["ok"]:
        frappe.db.set_value("Ecommerce Channel", channel.name, "last_orders_sync_at", now, update_modified=False)
    log_event(
        f"ecommerce:{channel.name}",
        "success" if result["ok"] else "error",
        "Shop-Express orders synchronized",
        direction="in",
        response_payload=result,
    )
    return result


def pull_customers(channel) -> dict:
    now = frappe.utils.now_datetime()
    start = _sync_start(
        channel.last_customers_sync_at,
        now,
        channel.orders_overlap_minutes,
        first_days=channel.initial_sync_days or 7,
    )
    page_size = max(1, min(int(channel.orders_page_size or 100), 500))
    max_pages = max(1, min(int(channel.orders_max_pages or 50), 200))
    api = client_for(channel)
    customers = []
    for page in range(max_pages):
        data = api.export_users(
            **{
                "from": start.strftime("%Y-%m-%d"),
                "to": now.strftime("%Y-%m-%d"),
                "limit": page_size,
                "offset": page * page_size,
            }
        )
        rows = (data.get("response") or {}).get("users") or []
        if not isinstance(rows, list):
            raise ValueError("Shop-Express users response must contain a list")
        customers.extend(_normalize_customer(row) for row in rows)
        if len(rows) < page_size:
            break
    else:
        raise RuntimeError("Shop-Express user pagination exceeded the configured maximum")

    result = import_customers(channel, customers)
    if result["ok"]:
        frappe.db.set_value("Ecommerce Channel", channel.name, "last_customers_sync_at", now, update_modified=False)
    log_event(
        f"ecommerce:{channel.name}",
        "success" if result["ok"] else "error",
        "Shop-Express customers synchronized",
        direction="in",
        response_payload=result,
    )
    return result


def push_stock(channel) -> dict:
    _, products = get_catalog(channel)
    batch_size = max(1, min(int(channel.api_batch_size or 200), 500))
    api = client_for(channel)
    pushed = 0
    warnings = []
    for start in range(0, len(products), batch_size):
        batch = products[start : start + batch_size]
        payload = [
            {
                "sku": row["sku"],
                "price": f"{float(row.get('price') or 0):.2f}",
                "residues": max(0, int(row.get("quantity") or 0)),
                "presence": 1 if row.get("available") else 0,
                "display_in_showcase": 1,
            }
            for row in batch
        ]
        response = api.update_residues(payload)
        warnings.extend(_batch_warnings(response, payload, "stock update"))
        pushed += len(batch)
    result = {"ok": not warnings, "pushed": pushed, "batches": (pushed + batch_size - 1) // batch_size, "warnings": warnings}
    if result["ok"]:
        frappe.db.set_value("Ecommerce Channel", channel.name, "last_stock_sync_at", frappe.utils.now_datetime(), update_modified=False)
    log_event(
        f"ecommerce:{channel.name}",
        "success" if result["ok"] else "error",
        "Shop-Express prices and stock synchronized",
        response_payload=result,
    )
    return result


def push_catalog(channel) -> dict:
    categories, products = get_catalog(channel)
    category_by_id = {str(row["id"]): row for row in categories}
    batch_size = max(1, min(int(channel.api_batch_size or 200), 500))
    api = client_for(channel)
    pushed = 0
    warnings = []
    for start in range(0, len(products), batch_size):
        batch = products[start : start + batch_size]
        payload = []
        for row in batch:
            product = {
                "title": {"uk": row["name"]},
                "description": {"uk": row.get("description") or ""},
                "unit": {"uk": row.get("uom") or "шт."},
                "sku": row["sku"],
                "currency": row.get("currency") or channel.currency or "UAH",
                "price": f"{float(row.get('price') or 0):.2f}",
                "residues": max(0, int(row.get("quantity") or 0)),
                "presence": 1 if row.get("available") else 0,
                "display_in_showcase": 1,
            }
            if row.get("parent_sku"):
                product["parent_sku"] = row["parent_sku"]
                product["parent_title"] = {"uk": row.get("parent_name") or row["name"]}
            if row.get("barcode"):
                product["barcode"] = row["barcode"]
            category_path = _category_path(str(row.get("category_id") or ""), category_by_id)
            if category_path:
                product["parent"] = category_path
            if row.get("brand"):
                product["brand"] = {
                    "external_id": str(row["brand"]),
                    "value": {"uk": row["brand"]},
                }
            if row.get("pictures"):
                product["images"] = {"links": row["pictures"], "replace": 1}
            payload.append(product)
        response = api.import_products(payload)
        warnings.extend(_batch_warnings(response, payload, "catalog import"))
        pushed += len(batch)
    result = {"ok": not warnings, "pushed": pushed, "batches": (pushed + batch_size - 1) // batch_size, "warnings": warnings}
    if result["ok"]:
        frappe.db.set_value("Ecommerce Channel", channel.name, "last_catalog_sync_at", frappe.utils.now_datetime(), update_modified=False)
    log_event(
        f"ecommerce:{channel.name}",
        "success" if result["ok"] else "error",
        "Shop-Express catalog synchronized",
        response_payload=result,
    )
    return result


def push_order_statuses(channel) -> dict:
    mappings = {
        row.erpnext_status: str(row.external_status_id).strip()
        for row in (channel.get("status_mappings") or [])
        if int(row.push_to_channel or 0) and row.erpnext_status and row.external_status_id
    }
    if not mappings:
        return {"ok": True, "skipped": True, "reason": "no outgoing status mappings"}
    filters = {
        "ua_ecommerce_channel": channel.name,
        "ua_external_order_id": ["is", "set"],
        "status": ["in", list(mappings)],
    }
    if channel.last_order_status_sync_at:
        filters["modified"] = [">", channel.last_order_status_sync_at]
    rows = frappe.get_all(
        "Sales Order",
        filters=filters,
        fields=["name", "ua_external_order_id", "status", "modified"],
        order_by="modified asc",
        limit_page_length=10_000,
    )
    if len(rows) >= 10_000:
        raise RuntimeError("Shop-Express status synchronization reached the safety limit")
    if not rows:
        return {"ok": True, "pushed": 0}
    batch_size = max(1, min(int(channel.api_batch_size or 200), 500))
    api = client_for(channel)
    pushed = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        payload = [
            {
                "order_id": int(row.ua_external_order_id) if str(row.ua_external_order_id).isdigit() else row.ua_external_order_id,
                "status": int(mappings[row.status]) if mappings[row.status].isdigit() else mappings[row.status],
            }
            for row in batch
        ]
        response = api.update_orders(payload)
        log_rows = (response.get("response") or {}).get("log") or []
        if not isinstance(log_rows, list) or len(log_rows) != len(payload):
            raise RuntimeError("Shop-Express returned an incomplete order status update log")
        rejected = [row for row in log_rows if str(row.get("status") or "").upper() != "OK"]
        if rejected:
            raise RuntimeError(f"Shop-Express rejected order status updates: {rejected[:10]}")
        pushed += len(batch)
    watermark = max(row.modified for row in rows)
    frappe.db.set_value(
        "Ecommerce Channel",
        channel.name,
        "last_order_status_sync_at",
        watermark,
        update_modified=False,
    )
    result = {"ok": True, "pushed": pushed, "batches": (pushed + batch_size - 1) // batch_size}
    log_event(
        f"ecommerce:{channel.name}",
        "success",
        "Shop-Express order statuses synchronized",
        response_payload=result,
    )
    return result


def _normalize_order(row: dict) -> dict:
    status = row.get("stat_status") or {}
    return {
        "external_id": row.get("order_id"),
        "number": row.get("order_number"),
        "created_at": row.get("stat_created"),
        "currency": row.get("currency") or "UAH",
        "status": status.get("id") if isinstance(status, dict) else status,
        "paid": row.get("payed"),
        "comment": row.get("comment"),
        "customer": {
            "external_id": row.get("user"),
            "name": row.get("delivery_name"),
            "phone": row.get("delivery_phone"),
            "email": row.get("delivery_email"),
        },
        "delivery": {"city": row.get("delivery_city"), "address": row.get("delivery_address")},
        "items": [
            {
                "sku": item.get("sku") or item.get("id"),
                "quantity": item.get("quantity"),
                "price": item.get("price"),
            }
            for item in (row.get("product") or [])
            if isinstance(item, dict)
        ],
    }


def _normalize_customer(row: dict) -> dict:
    return {
        "external_id": row.get("id"),
        "name": row.get("title"),
        "email": row.get("email"),
        "phone": row.get("phone"),
        "addresses": row.get("addresses") or [],
    }


def _sync_start(previous, now, overlap_minutes, *, first_days: int):
    if previous:
        return frappe.utils.get_datetime(previous) - timedelta(minutes=max(0, int(overlap_minutes or 0)))
    return now - timedelta(days=first_days)


def _batch_warnings(response: dict, payload: list[dict], operation: str) -> list[dict]:
    response_body = response.get("response") or {}
    log_rows = response_body.get("log") if isinstance(response_body, dict) else None
    if log_rows is not None:
        if not isinstance(log_rows, list) or len(log_rows) != len(payload):
            raise RuntimeError(f"Shop-Express returned an incomplete {operation} log")
        return [row for row in log_rows if not isinstance(row, dict) or str(row.get("status") or "").upper() != "OK"]
    if response.get("status") == "WARNING":
        return [response_body if isinstance(response_body, dict) else {"warning": str(response_body)[:500]}]
    return []


def _category_path(category_id: str, category_by_id: dict[str, dict]) -> list[dict]:
    path = []
    current = category_by_id.get(category_id)
    seen = set()
    while current and str(current.get("id")) not in seen:
        seen.add(str(current["id"]))
        external_id = str(current.get("name_key") or "").strip()
        if external_id:
            row = {
                "external_id": external_id,
                "value": {"uk": current.get("name") or external_id},
            }
            parent = category_by_id.get(str(current.get("parent_id") or ""))
            if parent and parent.get("name_key"):
                row["parent_external_id"] = str(parent["name_key"])
            path.append(row)
        current = category_by_id.get(str(current.get("parent_id") or ""))
    return list(reversed(path))
