from __future__ import annotations

import hashlib
import mimetypes
import posixpath
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

import frappe

MAX_CATALOG_ITEMS = 100_000
MAX_PHOTOS_PER_ITEM = 20
_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class PhotoAsset:
    item: str
    remote_name: str
    content: bytes
    idempotency_key: str
    public_url: str


def collect_records(
    settings,
    *,
    include_photos: bool = True,
) -> tuple[list[dict], dict[str, object], list[PhotoAsset]]:
    """Build flat layout records and automatic channel mappings for one store."""
    channel_key = f"OcStore Settings:{settings.name}"
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
        raise RuntimeError("ocStore catalog reached the configured safety limit")
    mappings = frappe.get_all(
        "Ecommerce Item Mapping",
        filters={"channel": channel_key},
        fields=[
            "name",
            "item",
            "external_id",
            "variant_sku",
            "last_export_hash",
            "export_hash_state",
            "sync_status",
        ],
        limit_page_length=MAX_CATALOG_ITEMS * 2,
    )
    mapping_by_item = _mapping_by_item(mappings)
    if int(settings.export_all_items or 0):
        for item in items:
            if item.name not in mapping_by_item:
                mapping_by_item[item.name] = _create_mapping(channel_key, item)
        items = [item for item in items if mapping_by_item[item.name].sync_status != "Disabled"]
    else:
        items = [item for item in items if item.name in mapping_by_item and mapping_by_item[item.name].sync_status != "Disabled"]
    if not items:
        raise RuntimeError("No ERPNext items are selected for this ocStore instance")

    item_names = [item.name for item in items]
    prices = _prices(settings, item_names)
    quantities = _quantities(settings, item_names)
    barcodes = _barcodes(item_names)
    photo_urls, photo_assets = _photos(settings, items, mapping_by_item, include_photos=include_photos)
    records = []
    for item in items:
        mapping = mapping_by_item[item.name]
        quantity = max(0, float(quantities.get(item.name, 0)))
        records.append(
            {
                "item": item.name,
                "external_id": mapping.external_id,
                "variant_sku": mapping.variant_sku or item.item_code or item.name,
                "sku": mapping.variant_sku or item.item_code or item.name,
                "name": item.item_name or item.item_code or item.name,
                "description": item.description or "",
                "category": item.item_group or "",
                "brand": item.brand or "",
                "barcode": barcodes.get(item.name, ""),
                "uom": item.stock_uom or "",
                "parent_sku": _parent_sku(item, mapping_by_item),
                "price": float(prices.get(item.name, 0)),
                "currency": settings.currency or "UAH",
                "quantity": quantity,
                "available": "1" if quantity > 0 else "0",
                "photo_urls": "|".join(photo_urls.get(item.name, [])),
            }
        )
    return records, mapping_by_item, photo_assets


def _mapping_by_item(mappings: list) -> dict[str, object]:
    result = {}
    for mapping in mappings:
        if mapping.item in result:
            raise ValueError(f"Item has multiple mappings for this ocStore instance: {mapping.item}")
        result[mapping.item] = mapping
    return result


def _create_mapping(channel_key: str, item):
    external_id = str(item.item_code or item.name).strip()
    doc = frappe.get_doc(
        {
            "doctype": "Ecommerce Item Mapping",
            "item": item.name,
            "channel": channel_key,
            "external_id": external_id,
            "variant_sku": external_id,
            "sync_status": "Pending",
        }
    )
    try:
        doc.insert(ignore_permissions=True)
        return doc
    except frappe.DuplicateEntryError:
        name = frappe.db.get_value(
            "Ecommerce Item Mapping",
            {"channel": channel_key, "external_id": external_id},
            "name",
        )
        if name:
            existing = frappe.get_doc("Ecommerce Item Mapping", name)
            if existing.item != item.name:
                raise ValueError(
                    f"External ID {external_id} is already mapped to another ERPNext Item"
                ) from None
            return existing
        raise


def _prices(settings, item_names: list[str]) -> dict[str, float]:
    rows = frappe.get_all(
        "Item Price",
        filters={
            "price_list": settings.selling_price_list,
            "selling": 1,
            "item_code": ["in", item_names],
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
        if row.currency and str(row.currency).upper() != str(settings.currency or "UAH").upper():
            continue
        if row.valid_from and row.valid_from > today:
            continue
        if row.valid_upto and row.valid_upto < today:
            continue
        result[row.item_code] = float(row.price_list_rate or 0)
    return result


def _quantities(settings, item_names: list[str]) -> dict[str, float]:
    warehouses = [
        row.warehouse
        for row in (settings.get("warehouses") or [])
        if int(row.enabled or 0) and row.warehouse
    ]
    if not warehouses:
        return {}
    rows = frappe.get_all(
        "Bin",
        filters={"item_code": ["in", item_names], "warehouse": ["in", warehouses]},
        fields=["item_code", "sum(actual_qty) as actual_qty", "sum(reserved_qty) as reserved_qty"],
        group_by="item_code",
        limit_page_length=MAX_CATALOG_ITEMS,
    )
    return {
        row.item_code: max(0, float(row.actual_qty or 0) - float(row.reserved_qty or 0))
        for row in rows
    }


def _barcodes(item_names: list[str]) -> dict[str, str]:
    rows = frappe.get_all(
        "Item Barcode",
        filters={"parent": ["in", item_names], "parenttype": "Item"},
        fields=["parent", "barcode", "idx"],
        order_by="parent asc, idx asc",
        limit_page_length=MAX_CATALOG_ITEMS * 2,
    )
    result = {}
    for row in rows:
        if row.parent not in result and row.barcode:
            result[row.parent] = row.barcode
    return result


def _photos(
    settings,
    items: list,
    mapping_by_item: dict[str, object],
    *,
    include_photos: bool,
):
    photos_enabled = any(
        row.entity == "Photos" and int(row.enabled or 0) and row.method == "File"
        for row in (settings.get("sync_entities") or [])
    )
    if not photos_enabled or not include_photos:
        return {}, []
    item_names = [item.name for item in items]
    rows = frappe.get_all(
        "File",
        filters={
            "attached_to_doctype": "Item",
            "attached_to_name": ["in", item_names],
            "is_folder": 0,
        },
        fields=["name", "file_name", "file_url", "attached_to_name"],
        order_by="attached_to_name asc, creation asc",
        limit_page_length=min(MAX_CATALOG_ITEMS * MAX_PHOTOS_PER_ITEM, 200_000),
    )
    by_item: dict[str, list] = {}
    by_url = {str(row.file_url or ""): row for row in rows}
    for row in rows:
        by_item.setdefault(row.attached_to_name, []).append(row)
    for item in items:
        if item.image and item.image not in by_url:
            file_name = frappe.db.get_value("File", {"file_url": item.image}, "name")
            if file_name:
                by_item.setdefault(item.name, []).insert(0, frappe.get_doc("File", file_name))

    urls: dict[str, list[str]] = {}
    assets: list[PhotoAsset] = []
    for item in items:
        mapping = mapping_by_item[item.name]
        seen = set()
        for file_row in by_item.get(item.name, [])[:MAX_PHOTOS_PER_ITEM]:
            file_doc = frappe.get_doc("File", file_row.name)
            original_name = str(file_doc.file_name or posixpath.basename(file_doc.file_url or "image"))
            if not _is_image(original_name, getattr(file_doc, "content_type", "")):
                continue
            content = file_doc.get_content()
            raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
            digest = hashlib.sha256(raw).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            suffix = posixpath.splitext(original_name)[1].lower()[:10] or ".img"
            stem = _safe_stem(mapping.external_id)
            remote_name = f"{stem}-{len(seen):02d}-{digest[:16]}{suffix}"[:240]
            public_url = f"{settings.photo_url_prefix.rstrip('/')}/{quote(remote_name)}"
            key = f"ecom:photo:{hashlib.sha256(f'{settings.name}:{item.name}:{digest}'.encode()).hexdigest()}"
            assets.append(
                PhotoAsset(
                    item=item.name,
                    remote_name=remote_name,
                    content=raw,
                    idempotency_key=key,
                    public_url=public_url,
                )
            )
            urls.setdefault(item.name, []).append(public_url)
    return urls, assets


def _parent_sku(item, mapping_by_item: dict[str, object]) -> str:
    if not item.variant_of:
        return ""
    mapping = mapping_by_item.get(item.variant_of)
    return str(mapping.variant_sku or mapping.external_id) if mapping else str(item.variant_of)


def _safe_stem(value: str) -> str:
    result = _SAFE_STEM.sub("-", str(value or "item")).strip("-._")
    return (result or "item")[:120]


def _is_image(filename: str, content_type: str) -> bool:
    guessed = mimetypes.guess_type(filename)[0] or ""
    return str(content_type or guessed).lower().startswith("image/")
