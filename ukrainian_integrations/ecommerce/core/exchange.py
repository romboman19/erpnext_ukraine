from __future__ import annotations

import csv
import io
from datetime import datetime

# ElementTree only constructs trusted outbound XML; defusedxml parses inbound payloads.
from xml.etree import ElementTree as ET  # nosec B405

from defusedxml.ElementTree import fromstring as safe_xml_fromstring

MAX_EXCHANGE_BYTES = 24 * 1024 * 1024


def build_yml_catalog(
    *,
    channel_name: str,
    company: str,
    store_url: str,
    currency: str,
    categories: list[dict],
    products: list[dict],
    generated_at: datetime | None = None,
) -> bytes:
    generated_at = generated_at or datetime.now().astimezone()
    root = ET.Element("yml_catalog", {"date": generated_at.strftime("%Y-%m-%d %H:%M")})
    shop = ET.SubElement(root, "shop")
    _text(shop, "name", channel_name)
    _text(shop, "company", company)
    _text(shop, "url", store_url)
    currencies = ET.SubElement(shop, "currencies")
    ET.SubElement(currencies, "currency", {"id": currency, "rate": "1"})
    categories_el = ET.SubElement(shop, "categories")
    for category in categories:
        attrs = {"id": str(category["id"])}
        if category.get("parent_id"):
            attrs["parentId"] = str(category["parent_id"])
        element = ET.SubElement(categories_el, "category", attrs)
        element.text = str(category.get("name") or category["id"])

    offers = ET.SubElement(shop, "offers")
    for product in products:
        offer = ET.SubElement(
            offers,
            "offer",
            {
                "id": str(product["external_id"]),
                "available": "true" if product.get("available") else "false",
            },
        )
        if product.get("url"):
            _text(offer, "url", product["url"])
        _text(offer, "price", _decimal(product.get("price", 0)))
        _text(offer, "currencyId", product.get("currency") or currency)
        _text(offer, "categoryId", product.get("category_id") or "all")
        for picture in product.get("pictures") or []:
            if picture:
                _text(offer, "picture", picture)
        _text(offer, "name", product.get("name") or product["sku"])
        if product.get("brand"):
            _text(offer, "vendor", product["brand"])
        _text(offer, "vendorCode", product["sku"])
        if product.get("description"):
            _text(offer, "description", product["description"])
        if product.get("uom"):
            _text(offer, "unit", product["uom"])
        if product.get("barcode"):
            _text(offer, "barcode", product["barcode"])
        _text(offer, "quantity", str(max(0, int(product.get("quantity") or 0))))
        if product.get("parent_sku"):
            _text(offer, "param", product["parent_sku"], {"name": "parent_sku"})
    return _serialize(root)


def build_canonical_catalog(
    *,
    channel_name: str,
    currency: str,
    categories: list[dict],
    products: list[dict],
    generated_at: datetime | None = None,
) -> bytes:
    generated_at = generated_at or datetime.now().astimezone()
    root = ET.Element(
        "ecommerce_exchange",
        {
            "schema": "erpnext-ecommerce-v1",
            "entity": "catalog",
            "channel": channel_name,
            "generated_at": generated_at.isoformat(timespec="seconds"),
        },
    )
    categories_el = ET.SubElement(root, "categories")
    for category in categories:
        ET.SubElement(
            categories_el,
            "category",
            {
                "id": str(category["id"]),
                "parent_id": str(category.get("parent_id") or ""),
                "name": str(category.get("name") or category["id"]),
            },
        )
    products_el = ET.SubElement(root, "products")
    for product in products:
        element = ET.SubElement(
            products_el,
            "product",
            {
                "id": str(product["external_id"]),
                "sku": str(product["sku"]),
                "available": "1" if product.get("available") else "0",
            },
        )
        fields = (
            "name",
            "description",
            "category_id",
            "brand",
            "barcode",
            "uom",
            "parent_sku",
            "url",
        )
        for field in fields:
            if product.get(field) not in (None, ""):
                _text(element, field, product[field])
        _text(element, "price", _decimal(product.get("price", 0)), {"currency": product.get("currency") or currency})
        _text(element, "quantity", str(max(0, int(product.get("quantity") or 0))))
        pictures = ET.SubElement(element, "pictures")
        for picture in product.get("pictures") or []:
            if picture:
                _text(pictures, "picture", picture)
    return _serialize(root)


def build_canonical_stock(
    *, channel_name: str, products: list[dict], generated_at: datetime | None = None
) -> bytes:
    generated_at = generated_at or datetime.now().astimezone()
    root = ET.Element(
        "ecommerce_exchange",
        {
            "schema": "erpnext-ecommerce-v1",
            "entity": "prices_and_stock",
            "channel": channel_name,
            "generated_at": generated_at.isoformat(timespec="seconds"),
        },
    )
    rows = ET.SubElement(root, "products")
    for product in products:
        ET.SubElement(
            rows,
            "product",
            {
                "id": str(product["external_id"]),
                "sku": str(product["sku"]),
                "price": _decimal(product.get("price", 0)),
                "currency": str(product.get("currency") or "UAH"),
                "quantity": str(max(0, int(product.get("quantity") or 0))),
                "available": "1" if product.get("available") else "0",
            },
        )
    return _serialize(root)


def parse_orders_xml(content: bytes | str) -> list[dict]:
    raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    _validate_xml_input(raw)
    try:
        root = safe_xml_fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("Order exchange file contains invalid XML") from exc
    if _local_name(root.tag) == "order":
        order_elements = [root]
    else:
        order_elements = [element for element in root.iter() if _local_name(element.tag) == "order"]
    if not order_elements:
        raise ValueError("Order exchange file contains no order elements")
    if len(order_elements) > 10_000:
        raise ValueError("Order exchange file contains too many orders")
    return [_parse_order(element) for element in order_elements]


def parse_orders_csv(content: bytes | str) -> list[dict]:
    raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    if len(raw) > MAX_EXCHANGE_BYTES:
        raise ValueError("Order exchange file is too large")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Order CSV file must use UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text))
    grouped: dict[str, dict] = {}
    for index, row in enumerate(reader, start=1):
        if index > 100_000:
            raise ValueError("Order CSV file contains too many rows")
        order_id = _row_value(row, "order_id", "id", "order_number")
        if not order_id:
            raise ValueError(f"Order CSV row {index} has no order_id")
        order = grouped.setdefault(
            order_id,
            {
                "external_id": order_id,
                "number": _row_value(row, "order_number", "number") or order_id,
                "currency": _row_value(row, "currency") or "UAH",
                "status": _row_value(row, "status"),
                "customer": {
                    "external_id": _row_value(row, "customer_id", "user_id"),
                    "name": _row_value(row, "customer_name", "name"),
                    "phone": _row_value(row, "phone", "customer_phone"),
                    "email": _row_value(row, "email", "customer_email"),
                },
                "delivery": {
                    "city": _row_value(row, "delivery_city", "city"),
                    "address": _row_value(row, "delivery_address", "address"),
                },
                "items": [],
            },
        )
        sku = _row_value(row, "sku", "product_sku", "model")
        if not sku:
            raise ValueError(f"Order CSV row {index} has no SKU")
        order["items"].append(
            {
                "sku": sku,
                "quantity": _row_value(row, "quantity", "qty") or "0",
                "price": _row_value(row, "price", "rate") or "0",
                "name": _row_value(row, "product_name", "item_name"),
            }
        )
    if not grouped:
        raise ValueError("Order CSV file contains no data rows")
    return list(grouped.values())


def _parse_order(element: ET.Element) -> dict:
    customer = _first_child(element, "customer", "user", "client")
    delivery = _first_child(element, "delivery", "shipping")
    item_elements = [
        child
        for child in element.iter()
        if child is not element and _local_name(child.tag) in {"item", "product", "order_product"}
    ]
    if not item_elements:
        raise ValueError(f"Order {_value(element, 'id', 'order_id') or '?'} contains no products")
    return {
        "external_id": _value(element, "id", "order_id"),
        "number": _value(element, "number", "order_number"),
        "created_at": _value(element, "created_at", "created", "date_added"),
        "currency": _value(element, "currency", "currency_code") or "UAH",
        "status": _value(element, "status", "order_status"),
        "paid": _value(element, "paid", "payed"),
        "comment": _value(element, "comment", "note"),
        "customer": {
            "external_id": _value(customer, "id", "customer_id", "user_id"),
            "name": _value(customer, "name", "title", "customer_name"),
            "phone": _value(customer, "phone", "telephone"),
            "email": _value(customer, "email"),
        },
        "delivery": {
            "city": _value(delivery, "city", "delivery_city", "shipping_city"),
            "address": _value(delivery, "address", "delivery_address", "shipping_address"),
        },
        "items": [
            {
                "sku": _value(item, "sku", "model", "vendor_code", "external_id"),
                "quantity": _value(item, "quantity", "qty"),
                "price": _value(item, "price", "rate"),
                "name": _value(item, "name", "title", "product_name"),
            }
            for item in item_elements
        ],
    }


def _validate_xml_input(raw: bytes) -> None:
    if len(raw) > MAX_EXCHANGE_BYTES:
        raise ValueError("Order exchange file is too large")
    normalized = raw.upper()
    if b"<!DOCTYPE" in normalized or b"<!ENTITY" in normalized:
        raise ValueError("DTD and XML entities are not allowed in exchange files")


def _value(element: ET.Element | None, *names: str) -> str:
    if element is None:
        return ""
    normalized = {name.casefold() for name in names}
    for key, value in element.attrib.items():
        if _local_name(key).casefold() in normalized and value is not None:
            return str(value).strip()
    for child in element:
        if _local_name(child.tag).casefold() in normalized:
            return (child.text or "").strip()
    return ""


def _first_child(element: ET.Element, *names: str) -> ET.Element | None:
    normalized = {name.casefold() for name in names}
    return next(
        (child for child in element if _local_name(child.tag).casefold() in normalized),
        None,
    )


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _row_value(row: dict, *names: str) -> str:
    normalized = {str(key).strip().casefold(): value for key, value in row.items()}
    for name in names:
        value = normalized.get(name.casefold())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _text(parent: ET.Element, tag: str, value, attrs: dict | None = None) -> ET.Element:
    element = ET.SubElement(parent, tag, attrs or {})
    element.text = str(value or "")
    return element


def _decimal(value) -> str:
    return f"{float(value or 0):.2f}"


def _serialize(root: ET.Element) -> bytes:
    if hasattr(ET, "indent"):
        ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
