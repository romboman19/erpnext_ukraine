from __future__ import annotations

from typing import Any

# ElementTree constants are used only to inspect defusedxml-created nodes.
from xml.etree import ElementTree as ET  # nosec B405

from defusedxml.ElementTree import fromstring as safe_xml_fromstring

from ukrainian_integrations.ecommerce.base.serializers import get_serializer
from ukrainian_integrations.ecommerce.base.serializers.common import ensure_bounded, value


def parse_order_file(payload: bytes, layout: Any) -> list[dict]:
    raw = bytes(payload)
    ensure_bounded(raw)
    normalized = raw.upper()
    if b"<!DOCTYPE" in normalized or b"<!ENTITY" in normalized:
        raise ValueError("DTD and XML entities are not allowed in ocStore order files")
    try:
        root = safe_xml_fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("ocStore order XML is invalid") from exc

    flat_orders = get_serializer("XML").deserialize(raw, layout)
    item_element = str(value(layout, "item_element", "order") or "order")
    order_nodes = [node for node in root if _local_name(node.tag) == item_element]
    if len(flat_orders) != len(order_nodes) or not flat_orders:
        raise ValueError("ocStore order XML contains no importable orders")
    result = []
    seen = set()
    for flat, node in zip(flat_orders, order_nodes, strict=True):
        order = _with_common_fallbacks(flat, node)
        order["items"] = _items(node)
        order_id = str(order.get("channel_order_id") or "").strip()
        if order_id in seen:
            raise ValueError(f"ocStore order file contains duplicate order ID: {order_id}")
        seen.add(order_id)
        result.append(order)
    return result


def _with_common_fallbacks(order: dict, node) -> dict:
    customer = order.setdefault("customer", {})
    shipping = order.setdefault("shipping_address", {})
    payment = order.setdefault("payment", {})
    order["channel_order_id"] = order.get("channel_order_id") or _first(
        node, "external_id", "order_id", "id"
    )
    order["channel_status"] = order.get("channel_status") or _first(
        node, "status", "order_status", "order_status_id"
    )
    order["currency"] = order.get("currency") or _first(node, "currency_code", "currency")
    first_name = _first(node, "firstname", "first_name")
    last_name = _first(node, "lastname", "last_name")
    customer["name"] = customer.get("name") or " ".join(
        part for part in (first_name, last_name) if part
    )
    customer["phone"] = customer.get("phone") or _first(node, "telephone", "phone", "mobile")
    customer["email"] = customer.get("email") or _first(node, "email")
    shipping["city"] = shipping.get("city") or _first(node, "shipping_city", "city")
    shipping["address"] = shipping.get("address") or _first(
        node, "shipping_address_1", "shipping_address", "address"
    )
    payment["payment_type"] = payment.get("payment_type") or _first(
        node, "payment_code", "payment_method", "payment_type"
    )
    payment["amount"] = payment.get("amount") or _first(node, "total", "payment_amount") or 0
    payment["currency"] = payment.get("currency") or order.get("currency")
    payment["paid"] = payment.get("paid") or _first(node, "paid", "is_paid") or "0"
    order["comment"] = order.get("comment") or _first(node, "comment", "customer_comment")
    return order


def _items(order_node) -> list[dict]:
    containers = {
        "products",
        "items",
        "order_products",
        "order-products",
    }
    item_names = {"product", "item", "order_product", "order-product"}
    candidates = []
    for child in order_node:
        local = _local_name(child.tag)
        if local in containers:
            candidates.extend(node for node in child if _local_name(node.tag) in item_names)
        elif local in item_names:
            candidates.append(child)
    rows = []
    for item in candidates:
        model = _first(item, "model", "sku", "variant_sku")
        external_id = _first(item, "external_id", "erp_external_id") or model or _first(
            item, "product_id", "id"
        )
        rows.append(
            {
                "external_id": external_id,
                "variant_sku": model,
                "quantity": _first(item, "quantity", "qty"),
                "price": _first(item, "price", "unit_price"),
                "name": _first(item, "name", "product_name"),
            }
        )
    if not rows:
        raise ValueError("ocStore order contains no product rows")
    return rows


def _first(node, *names: str) -> str:
    wanted = set(names)
    for child in node:
        if _local_name(child.tag) in wanted:
            return (child.text or "").strip()
    return ""


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]
