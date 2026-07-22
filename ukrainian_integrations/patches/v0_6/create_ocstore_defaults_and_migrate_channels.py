from __future__ import annotations

import frappe

from ukrainian_integrations.utils.operations import canonical_hash

LAYOUTS = {
    "OcStore Products XML v1": {
        "root_element": "products",
        "item_element": "product",
        "fields": [
            ("external_id", "external_id", "none", 1),
            ("variant_sku", "model", "none", 1),
            ("name", "name", "none", 1),
            ("description", "description", "none", 0),
            ("category", "category", "none", 0),
            ("brand", "manufacturer", "none", 0),
            ("barcode", "ean", "none", 0),
            ("uom", "unit", "none", 0),
            ("parent_sku", "parent_model", "none", 0),
            ("price", "price", "number_2dp", 1),
            ("currency", "currency", "none", 1),
            ("quantity", "quantity", "number_2dp", 1),
            ("available", "available", "none", 1),
            ("photo_urls", "images", "none", 0),
        ],
    },
    "OcStore Prices XML v1": {
        "root_element": "prices",
        "item_element": "product",
        "fields": [
            ("external_id", "external_id", "none", 1),
            ("variant_sku", "model", "none", 1),
            ("price", "price", "number_2dp", 1),
            ("currency", "currency", "none", 1),
        ],
    },
    "OcStore Stock XML v1": {
        "root_element": "stock",
        "item_element": "product",
        "fields": [
            ("external_id", "external_id", "none", 1),
            ("variant_sku", "model", "none", 1),
            ("quantity", "quantity", "number_2dp", 1),
            ("available", "available", "none", 1),
        ],
    },
    "OcStore Photos XML v1": {
        "root_element": "photos",
        "item_element": "product",
        "fields": [
            ("external_id", "external_id", "none", 1),
            ("variant_sku", "model", "none", 1),
            ("photo_urls", "images", "none", 0),
        ],
    },
    "OcStore Orders XML v1": {
        "root_element": "orders",
        "item_element": "order",
        "fields": [
            ("channel_order_id", "order_id", "none", 1),
            ("channel_status", "status", "none", 1),
            ("currency", "currency_code", "none", 0),
            ("customer.name", "customer_name", "none", 0),
            ("customer.phone", "telephone", "none", 1),
            ("customer.email", "email", "none", 0),
            ("shipping_address.city", "shipping_city", "none", 0),
            ("shipping_address.address", "shipping_address_1", "none", 0),
            ("payment.payment_type", "payment_code", "none", 0),
            ("payment.amount", "total", "none", 0),
            ("payment.paid", "paid", "none", 0),
            ("comment", "comment", "none", 0),
        ],
    },
}

ENTITY_LAYOUT = {
    "Products": "OcStore Products XML v1",
    "Prices": "OcStore Prices XML v1",
    "Stock": "OcStore Stock XML v1",
    "Photos": "OcStore Photos XML v1",
    "Orders": "OcStore Orders XML v1",
}


def execute() -> None:
    if not frappe.db.exists("DocType", "OcStore Settings"):
        return
    _ensure_layouts()
    if not frappe.db.exists("DocType", "Ecommerce Channel"):
        return
    legacy_names = frappe.get_all(
        "Ecommerce Channel",
        filters={"provider": "ocStore"},
        pluck="name",
        order_by="creation asc",
    )
    for legacy_name in legacy_names:
        legacy = frappe.get_doc("Ecommerce Channel", legacy_name)
        target = _ensure_settings(legacy)
        _move_references(legacy.name, target.name)


def _ensure_layouts() -> None:
    for name, definition in LAYOUTS.items():
        if frappe.db.exists("Ecommerce File Layout", name):
            continue
        doc = frappe.get_doc(
            {
                "doctype": "Ecommerce File Layout",
                "layout_name": name,
                "format": "XML",
                "encoding": "UTF-8",
                "root_element": definition["root_element"],
                "item_element": definition["item_element"],
            }
        )
        for erp_field, external_field, transform, required in definition["fields"]:
            doc.append(
                "fields",
                {
                    "erp_fieldname": erp_field,
                    "external_column": external_field,
                    "transform": transform,
                    "required": required,
                },
            )
        doc.insert(ignore_permissions=True)


def _ensure_settings(legacy):
    existing = frappe.db.get_value(
        "OcStore Settings",
        {"migration_source": legacy.name},
        "name",
    )
    if existing:
        return frappe.get_doc("OcStore Settings", existing)
    if frappe.db.exists("OcStore Settings", legacy.channel_name):
        raise RuntimeError(
            f"OcStore Settings name collision requires manual reconciliation: {legacy.channel_name}"
        )
    doc = frappe.get_doc(
        {
            "doctype": "OcStore Settings",
            "store_name": legacy.channel_name,
            # Legacy channels had no File Delivery Endpoint, so migration is
            # deliberately disabled until the administrator configures FTP.
            "enabled": 0,
            "company": legacy.company,
            "store_url": legacy.store_url,
            "currency": legacy.currency or "UAH",
            "selling_price_list": legacy.selling_price_list,
            "default_customer_group": legacy.customer_group,
            "default_territory": legacy.territory,
            "export_all_items": int(not int(legacy.export_only_mapped_items or 0)),
            "export_file_prefix": "ocstore",
            "orders_file_prefix": "orders-",
            "max_order_files_per_run": 20,
            "migration_source": legacy.name,
        }
    )
    for entity, layout in ENTITY_LAYOUT.items():
        doc.append(
            "sync_entities",
            {
                "entity": entity,
                "enabled": 0,
                "direction": "Import" if entity == "Orders" else "Export",
                "method": "File",
                "interval_minutes": 60 if entity == "Orders" else 1440,
                "file_format": "XML",
                "file_layout": layout,
            },
        )
    for row in legacy.get("warehouses") or []:
        if row.warehouse:
            doc.append("warehouses", {"warehouse": row.warehouse, "enabled": 1})
    seen_statuses = set()
    for row in legacy.get("status_mappings") or []:
        channel_status = str(row.external_status_id or row.external_status or "").strip()
        if channel_status and channel_status not in seen_statuses:
            seen_statuses.add(channel_status)
            doc.append(
                "order_status_map",
                {
                    "channel_status": channel_status,
                    "erp_action": "Create Sales Order",
                    "reserve_stock": 1,
                    "reserve_days": 3,
                },
            )
    doc.insert(ignore_permissions=True)
    return doc


def _move_references(legacy_name: str, settings_name: str) -> None:
    target_key = f"OcStore Settings:{settings_name}"
    old_mappings = frappe.get_all(
        "Ecommerce Item Mapping",
        filters={"channel": legacy_name},
        fields=["name", "item", "external_id"],
        limit_page_length=1_000_000,
    )
    for mapping in old_mappings:
        conflict = frappe.db.get_value(
            "Ecommerce Item Mapping",
            {"channel": target_key, "external_id": mapping.external_id},
            ["name", "item"],
            as_dict=True,
        )
        if conflict and conflict.name != mapping.name:
            raise RuntimeError(
                "Duplicate ocStore mapping requires manual reconciliation: "
                f"{legacy_name}/{mapping.external_id}"
            )
        frappe.db.set_value(
            "Ecommerce Item Mapping",
            mapping.name,
            {
                "channel": target_key,
                "mapping_key": canonical_hash({"channel": target_key, "item": mapping.item}),
                "external_mapping_key": canonical_hash(
                    {"channel": target_key, "external_id": mapping.external_id}
                ),
            },
            update_modified=False,
        )
    for doctype in ("Sales Order", "Sales Invoice"):
        if not frappe.db.has_column(doctype, "ua_ecommerce_channel"):
            continue
        rows = frappe.get_all(
            doctype,
            filters={"ua_ecommerce_channel": legacy_name},
            fields=["name", "ua_external_order_id"],
            limit_page_length=1_000_000,
        )
        for row in rows:
            values = {"ua_ecommerce_channel": target_key}
            external_order_id = str(row.ua_external_order_id or "").strip()
            if external_order_id:
                new_key = f"ecom:o:{canonical_hash({'channel': target_key, 'channel_order_id': external_order_id})}"
                conflict = frappe.db.get_value(
                    doctype,
                    {"ua_external_order_key": new_key},
                    "name",
                )
                if conflict and conflict != row.name:
                    raise RuntimeError(
                        f"Duplicate migrated ocStore order requires manual reconciliation: {doctype}/{external_order_id}"
                    )
                values["ua_external_order_key"] = new_key
            frappe.db.set_value(doctype, row.name, values, update_modified=False)
