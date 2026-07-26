from __future__ import annotations

import math

import frappe

from erpnext_ua.ecommerce.providers.prom_ua.api import PromUAClient
from erpnext_ua.integrations.utils.logger import log_event
from erpnext_ua.integrations.utils.operations import canonical_hash
from erpnext_ua.integrations.utils.validation import validate_allowed_host, validate_http_url


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _client() -> PromUAClient:
    token = _cfg("prom_ua_token")
    base = _cfg("prom_ua_api_base", "https://my.prom.ua/api/v1")
    if not token:
        raise RuntimeError("prom_ua_token is not configured")
    validate_http_url(base, "Prom.ua API Base")
    validate_allowed_host(
        base,
        "Prom.ua API Base",
        default_hosts={"my.prom.ua"},
        config_key="prom_ua_allowed_api_hosts",
    )
    return PromUAClient(token=token, base_url=base)


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = "38" + digits
    return f"+{digits}" if 10 <= len(digits) <= 15 else ""


def _valid_email(value: str) -> str:
    email = (value or "").strip().lower()
    if len(email) > 254 or email.count("@") != 1:
        return ""
    local, domain = email.split("@", 1)
    return email if local and "." in domain and not domain.startswith(".") and not domain.endswith(".") else ""


def _order_item_rows(order: dict) -> list[dict]:
    source_items = order.get("products") or order.get("items") or []
    if not isinstance(source_items, list) or not source_items:
        raise ValueError("Prom order has no product rows")
    rows = []
    for item in source_items:
        if not isinstance(item, dict):
            raise ValueError("Prom order contains an invalid product row")
        code = str(item.get("external_id") or item.get("sku") or item.get("article") or "").strip()
        if not code:
            raise ValueError("Prom order product has no ERP external_id/SKU")
        if not frappe.db.exists("Item", code):
            raise ValueError(f"Prom order product is not mapped to an ERP Item: {code}")
        try:
            qty = float(item.get("quantity") or 0)
            rate = float(item.get("price") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"Prom order product has invalid quantity/price: {code}") from None
        if not math.isfinite(qty) or not math.isfinite(rate) or qty <= 0 or rate < 0:
            raise ValueError(f"Prom order product has invalid quantity/price: {code}")
        rows.append({"item_code": code, "qty": qty, "rate": rate})
    return rows


def _import_order(order: dict) -> str:
    ext_id = str(order.get("id") or order.get("order_id") or "").strip()
    if not ext_id:
        return "skipped"
    if len(ext_id) > 64:
        raise ValueError("Prom order ID is too long")

    order_key = f"prom_ua:{ext_id}"
    if frappe.db.exists("Sales Order", {"ua_external_order_key": order_key}):
        return "skipped"

    legacy_orders = frappe.get_all(
        "Sales Order",
        filters={"po_no": ext_id, "ua_external_order_key": ["is", "not set"]},
        pluck="name",
        limit=2,
    )
    if len(legacy_orders) == 1:
        frappe.db.set_value(
            "Sales Order",
            legacy_orders[0],
            "ua_external_order_key",
            order_key,
            update_modified=False,
        )
        return "skipped"
    if len(legacy_orders) > 1:
        raise RuntimeError(f"Ambiguous legacy Prom order ID: {ext_id}")

    item_rows = _order_item_rows(order)

    company = (_cfg("prom_ua_company") or "").strip()
    customer_group = _cfg("prom_ua_customer_group") or frappe.db.get_single_value(
        "Selling Settings", "customer_group"
    )
    territory = _cfg("prom_ua_territory") or frappe.db.get_single_value("Selling Settings", "territory")
    if not company or not customer_group or not territory:
        raise RuntimeError("Configure prom_ua_company, prom_ua_customer_group and prom_ua_territory")
    expected_currency = str(_cfg("prom_ua_currency", "UAH") or "UAH").strip().upper()
    order_currency = str(order.get("currency") or order.get("currency_code") or expected_currency).strip().upper()
    if order_currency != expected_currency:
        raise ValueError(
            f"Prom order currency {order_currency} does not match configured currency {expected_currency}"
        )

    customer_name = " ".join(
        value.strip()
        for value in (
            str(order.get("client_first_name") or ""),
            str(order.get("client_second_name") or ""),
            str(order.get("client_last_name") or ""),
        )
        if value.strip()
    ) or str(order.get("client") or "Prom Customer")
    customer_external_id = str(order.get("client_id") or order.get("customer_id") or "").strip()
    phone = _normalize_phone(str(order.get("phone") or ""))
    email = _valid_email(str(order.get("email") or ""))
    if customer_external_id:
        customer_key = f"prom_ua:client:{canonical_hash({'client_id': customer_external_id})[:40]}"
    elif phone or email:
        customer_key = f"prom_ua:identity:{canonical_hash({'phone': phone, 'email': email})[:40]}"
    else:
        customer_key = f"prom_ua:order:{ext_id}"

    customer = frappe.db.get_value("Customer", {"ua_external_customer_key": customer_key}, "name")
    if not customer:
        customer_doc = frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": customer_name[:140],
                "customer_group": customer_group,
                "territory": territory,
                "mobile_no": phone or None,
                "email_id": email or None,
                "ua_external_customer_key": customer_key,
            }
        )
        try:
            customer_doc.insert(ignore_permissions=True)
            customer = customer_doc.name
        except frappe.DuplicateEntryError:
            customer = frappe.db.get_value(
                "Customer", {"ua_external_customer_key": customer_key}, "name"
            )
            if not customer:
                raise

    sales_order = frappe.new_doc("Sales Order")
    sales_order.company = company
    sales_order.customer = customer
    sales_order.po_no = ext_id
    sales_order.ua_external_order_key = order_key
    sales_order.currency = expected_currency
    sales_order.delivery_date = frappe.utils.nowdate()
    for row in item_rows:
        sales_order.append("items", row)
    try:
        sales_order.insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        if frappe.db.exists("Sales Order", {"ua_external_order_key": order_key}):
            return "skipped"
        raise
    return "created"


def pull_orders(limit: int = 50) -> dict:
    status_filter = _cfg("prom_ua_orders_status")
    page_size = max(1, min(int(limit or 50), 100))
    max_pages = max(1, min(int(_cfg("prom_ua_orders_max_pages", 20) or 20), 200))
    req = {"limit": page_size, "status": status_filter, "max_pages": max_pages}
    try:
        client = _client()
        orders = []
        last_id = None
        seen_cursors = set()
        for _page in range(1, max_pages + 1):
            data = client.list_orders(status=status_filter, limit=page_size, last_id=last_id)
            page_orders = data.get("orders") or data.get("data") or []
            if not isinstance(page_orders, list):
                raise ValueError("PromUA orders payload must contain a list")
            orders.extend(page_orders)
            if len(page_orders) < page_size or not page_orders:
                break
            ids = [int(order["id"]) for order in page_orders if str(order.get("id") or "").isdigit()]
            if not ids:
                raise RuntimeError("PromUA pagination page has no numeric order IDs")
            next_cursor = min(ids) - 1
            if next_cursor < 0 or next_cursor in seen_cursors:
                raise RuntimeError("PromUA pagination cursor is invalid or repeated")
            seen_cursors.add(next_cursor)
            last_id = next_cursor
        else:
            raise RuntimeError("PromUA order pagination exceeded max_pages")
        created = 0
        skipped = 0
        failed = 0

        for o in orders:
            frappe.db.savepoint("prom_order_import")
            try:
                outcome = _import_order(o)
            except Exception:
                frappe.db.rollback(save_point="prom_order_import")
                failed += 1
                log_event(
                    "prom_ua",
                    "error",
                    f"Order import failed: {o.get('id') or 'missing-id'}",
                    error_trace=frappe.get_traceback(),
                )
                continue
            created += int(outcome == "created")
            skipped += int(outcome == "skipped")

        out = {
            "ok": failed == 0,
            "received": len(orders),
            "created": created,
            "skipped": skipped,
            "failed": failed,
        }
        log_event("prom_ua", "success", "Orders synced", request_payload=req, response_payload=out)
        return out
    except Exception:
        log_event("prom_ua", "error", "Orders sync failed", request_payload=req, error_trace=frappe.get_traceback())
        raise


def push_stock(limit: int | None = None) -> dict:
    warehouses = _cfg("prom_ua_warehouses") or []
    if isinstance(warehouses, str):
        warehouses = [x.strip() for x in warehouses.split(",") if x.strip()]
    if not warehouses:
        raise RuntimeError("prom_ua_warehouses must explicitly list warehouses allowed for marketplace stock")
    configured_limit = limit if limit is not None else _cfg("prom_ua_stock_max_items", 100_000)
    max_items = max(1, min(int(configured_limit or 100_000), 1_000_000))
    batch_size = max(1, min(int(_cfg("prom_ua_stock_batch_size", 500) or 500), 1000))
    pushed = 0
    batches = 0
    try:
        client = _client()
        while pushed < max_items:
            take = min(batch_size, max_items - pushed)
            item_codes = frappe.get_all(
                "Item",
                filters={"disabled": 0},
                pluck="name",
                order_by="name asc",
                start=pushed,
                page_length=take,
            )
            if not item_codes:
                break
            stock_rows = frappe.get_all(
                "Bin",
                filters={
                    "item_code": ["in", item_codes],
                    "warehouse": ["in", warehouses],
                },
                fields=["item_code", "sum(actual_qty) as qty"],
                group_by="item_code",
            )
            stock_by_item = {row.item_code: float(row.qty or 0) for row in stock_rows}
            payload_rows = [
                {
                    "id": item_code,
                    "quantity_in_stock": max(0, int(stock_by_item.get(item_code, 0))),
                    "presence": "available" if stock_by_item.get(item_code, 0) > 0 else "not_available",
                }
                for item_code in item_codes
            ]
            if not payload_rows:
                break
            response = client.update_stock(payload_rows)
            processed = response.get("processed_ids") if isinstance(response, dict) else None
            errors = response.get("errors") if isinstance(response, dict) else None
            expected_ids = {str(row["id"]) for row in payload_rows}
            processed_ids = {str(value) for value in processed} if isinstance(processed, list) else set()
            if not isinstance(processed, list) or errors or processed_ids != expected_ids:
                raise RuntimeError(f"PromUA rejected stock batch: {response}")
            pushed += len(payload_rows)
            batches += 1
            if len(item_codes) < take:
                break
        out = {"ok": True, "pushed": pushed, "batches": batches}
        log_event("prom_ua", "success", "Stock pushed", request_payload={"count": pushed, "warehouses": warehouses}, response_payload={"ok": True, "batches": batches})
        return out
    except Exception:
        log_event("prom_ua", "error", "Stock push failed", request_payload={"pushed_before_failure": pushed, "warehouses": warehouses}, error_trace=frappe.get_traceback())
        raise
