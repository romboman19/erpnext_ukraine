from __future__ import annotations

from datetime import date

import frappe

MAX_CATALOG_ITEMS = 100_000


def get_catalog(channel) -> tuple[list[dict], list[dict]]:
    items = frappe.get_all(
        "Item",
        filters={"disabled": 0},
        fields=[
            "name",
            "item_code",
            "item_name",
            "description",
            "item_group",
            "stock_uom",
            "brand",
            "image",
            "variant_of",
        ],
        order_by="name asc",
        limit_page_length=MAX_CATALOG_ITEMS,
    )
    if len(items) >= MAX_CATALOG_ITEMS:
        raise RuntimeError("Ecommerce catalog reached the configured safety limit")

    mappings = frappe.get_all(
        "Ecommerce Item Mapping",
        filters={"channel": channel.name},
        fields=["item", "external_sku", "external_id", "enabled"],
        limit_page_length=MAX_CATALOG_ITEMS,
    )
    item_by_name = {row.name: row for row in items}
    mapping_by_item = {row.item: row for row in mappings}
    if int(channel.export_only_mapped_items or 0):
        items = [row for row in items if row.name in mapping_by_item and int(mapping_by_item[row.name].enabled or 0)]
    else:
        items = [row for row in items if row.name not in mapping_by_item or int(mapping_by_item[row.name].enabled or 0)]
    if not items:
        raise RuntimeError(
            "No items are selected for this ecommerce channel. Add enabled item mappings or disable Export Only Mapped Items."
        )
    item_codes = [row.name for row in items]
    prices = _prices(channel, item_codes)
    quantities = _quantities(channel, item_codes)
    barcodes = _barcodes(item_codes)
    categories = _categories({row.item_group for row in items if row.item_group})
    category_ids = {row["name_key"]: row["id"] for row in categories}

    products = []
    for item in items:
        mapping = mapping_by_item.get(item.name)
        sku = (mapping.external_sku if mapping else None) or item.item_code or item.name
        external_id = (mapping.external_id if mapping else None) or sku
        quantity = max(0, int(quantities.get(item.name, 0)))
        image = _absolute_url(item.image)
        products.append(
            {
                "item": item.name,
                "external_id": external_id,
                "sku": sku,
                "name": item.item_name or item.item_code or item.name,
                "description": item.description or "",
                "category_id": category_ids.get(item.item_group, "all"),
                "brand": item.brand or "",
                "barcode": barcodes.get(item.name, ""),
                "uom": item.stock_uom or "",
                "parent_sku": (
                    (mapping_by_item[item.variant_of].external_sku or item.variant_of)
                    if item.variant_of in mapping_by_item
                    else (item.variant_of or "")
                ),
                "parent_name": (
                    item_by_name[item.variant_of].item_name or item.variant_of
                    if item.variant_of in item_by_name
                    else ""
                ),
                "price": prices.get(item.name, 0),
                "currency": channel.currency or "UAH",
                "quantity": quantity,
                "available": quantity > 0,
                "pictures": [image] if image else [],
                "url": "",
            }
        )
    return categories, products


def _prices(channel, item_codes: list[str]) -> dict[str, float]:
    if not item_codes:
        return {}
    rows = frappe.get_all(
        "Item Price",
        filters={
            "price_list": channel.selling_price_list,
            "selling": 1,
            "item_code": ["in", item_codes],
        },
        fields=["item_code", "price_list_rate", "currency", "valid_from", "valid_upto", "modified"],
        order_by="modified desc",
        limit_page_length=MAX_CATALOG_ITEMS * 2,
    )
    today = date.today()
    result = {}
    for row in rows:
        if row.item_code in result:
            continue
        if row.currency and str(row.currency).upper() != str(channel.currency or "UAH").upper():
            continue
        if row.valid_from and row.valid_from > today:
            continue
        if row.valid_upto and row.valid_upto < today:
            continue
        result[row.item_code] = float(row.price_list_rate or 0)
    return result


def _quantities(channel, item_codes: list[str]) -> dict[str, float]:
    if not item_codes:
        return {}
    warehouse_rows = list(channel.get("warehouses") or [])
    warehouses = [row.warehouse for row in warehouse_rows if row.warehouse]
    if not warehouses:
        return {}
    safety_stock = sum(float(row.safety_stock or 0) for row in warehouse_rows)
    rows = frappe.get_all(
        "Bin",
        filters={"item_code": ["in", item_codes], "warehouse": ["in", warehouses]},
        fields=["item_code", "sum(actual_qty) as actual_qty", "sum(reserved_qty) as reserved_qty"],
        group_by="item_code",
        limit_page_length=MAX_CATALOG_ITEMS,
    )
    return {
        row.item_code: max(0, float(row.actual_qty or 0) - float(row.reserved_qty or 0) - safety_stock)
        for row in rows
    }


def _barcodes(item_codes: list[str]) -> dict[str, str]:
    if not item_codes:
        return {}
    rows = frappe.get_all(
        "Item Barcode",
        filters={"parent": ["in", item_codes], "parenttype": "Item"},
        fields=["parent", "barcode", "idx"],
        order_by="parent asc, idx asc",
        limit_page_length=MAX_CATALOG_ITEMS * 2,
    )
    result = {}
    for row in rows:
        if row.parent not in result and row.barcode:
            result[row.parent] = row.barcode
    return result


def _categories(item_groups: set[str]) -> list[dict]:
    if not item_groups:
        return [{"id": "all", "name": "All Products", "name_key": "", "parent_id": ""}]
    rows = frappe.get_all(
        "Item Group",
        fields=["name", "item_group_name", "parent_item_group", "lft"],
        order_by="lft asc",
        limit_page_length=10_000,
    )
    by_name = {row.name: row for row in rows}
    required = set(item_groups)
    for group in list(item_groups):
        current = by_name.get(group)
        seen = set()
        while current and current.parent_item_group and current.parent_item_group not in seen:
            seen.add(current.parent_item_group)
            required.add(current.parent_item_group)
            current = by_name.get(current.parent_item_group)
    identifiers = {name: str(index + 1) for index, name in enumerate(row.name for row in rows if row.name in required)}
    return [
        {
            "id": identifiers[row.name],
            "name": row.item_group_name or row.name,
            "parent_id": identifiers.get(row.parent_item_group, ""),
            "name_key": row.name,
        }
        for row in rows
        if row.name in required
    ]


def _absolute_url(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith(("https://", "http://")):
        return value
    return frappe.utils.get_url(value)
