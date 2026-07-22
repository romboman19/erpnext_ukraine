from __future__ import annotations

import hashlib
import json

import frappe
from frappe.utils.file_manager import save_file

from ukrainian_integrations.ecommerce.core.catalog import get_catalog
from ukrainian_integrations.ecommerce.core.exchange import (
    build_canonical_catalog,
    build_canonical_stock,
    build_yml_catalog,
    parse_orders_csv,
    parse_orders_xml,
)
from ukrainian_integrations.ecommerce.core.orders import import_orders
from ukrainian_integrations.utils.logger import log_event, sanitize_text


def generate_export(channel, entity: str) -> dict:
    if entity not in {"Catalog", "Prices and Stock"}:
        raise ValueError("Unsupported ecommerce export entity")
    categories, products = get_catalog(channel)
    if entity == "Catalog":
        profile = channel.catalog_xml_profile or "ERPNext Exchange XML v1"
        if profile == "Shop-Express YML":
            content = build_yml_catalog(
                channel_name=channel.channel_name,
                company=channel.company,
                store_url=channel.store_url or channel.api_base_url or "https://example.invalid",
                currency=channel.currency or "UAH",
                categories=categories,
                products=products,
            )
            extension = "yml"
            file_format = "YML"
        elif profile == "ERPNext Exchange XML v1":
            content = build_canonical_catalog(
                channel_name=channel.name,
                currency=channel.currency or "UAH",
                categories=categories,
                products=products,
            )
            extension = "xml"
            file_format = "XML"
        else:
            raise ValueError(f"Unsupported catalog XML profile: {profile}")
    else:
        profile = "ERPNext Exchange XML v1"
        content = build_canonical_stock(channel_name=channel.name, products=products)
        extension = "xml"
        file_format = "XML"

    checksum = hashlib.sha256(content).hexdigest()
    exchange = frappe.get_doc(
        {
            "doctype": "Ecommerce File Exchange",
            "channel": channel.name,
            "direction": "Export",
            "entity": entity,
            "file_format": file_format,
            "profile": profile,
            "status": "Draft",
            "exchange_file": "pending",
            "checksum": checksum,
            "row_count": len(products),
            "result": json.dumps({"ok": True, "rows": len(products)}, ensure_ascii=False),
        }
    ).insert(ignore_permissions=True)
    filename = f"{frappe.scrub(channel.name)}-{frappe.scrub(entity)}-{exchange.name}.{extension}"
    file_doc = save_file(filename, content, exchange.doctype, exchange.name, is_private=1)
    exchange.db_set(
        {
            "exchange_file": file_doc.file_url,
            "status": "Ready",
            "processed_at": frappe.utils.now_datetime(),
        },
        update_modified=False,
    )
    timestamp_field = "last_catalog_sync_at" if entity == "Catalog" else "last_stock_sync_at"
    frappe.db.set_value("Ecommerce Channel", channel.name, timestamp_field, frappe.utils.now_datetime(), update_modified=False)
    result = {"ok": True, "exchange": exchange.name, "file_url": file_doc.file_url, "rows": len(products), "checksum": checksum}
    log_event(
        f"ecommerce:{channel.name}",
        "success",
        f"{entity} exchange file generated",
        reference_doctype=exchange.doctype,
        reference_name=exchange.name,
        response_payload={"rows": len(products), "checksum": checksum},
    )
    return result


def process_import(exchange) -> dict:
    if exchange.direction != "Import" or exchange.entity != "Orders":
        raise ValueError("Only incoming order files can be processed")
    frappe.db.sql(
        "SELECT name FROM `tabEcommerce File Exchange` WHERE name = %s FOR UPDATE",
        (exchange.name,),
    )
    exchange.reload()
    if exchange.status == "Processed":
        try:
            saved = json.loads(exchange.result or "{}")
        except (TypeError, ValueError):
            saved = {"ok": True}
        return {**saved, "idempotent": True}
    exchange.db_set({"status": "Processing", "error": ""}, update_modified=False)
    try:
        file_name = frappe.db.get_value("File", {"file_url": exchange.exchange_file}, "name")
        if not file_name:
            raise ValueError("Attached exchange file cannot be found")
        file_doc = frappe.get_doc("File", file_name)
        content = file_doc.get_content()
        raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        checksum = hashlib.sha256(raw).hexdigest()
        exchange.db_set("checksum", checksum, update_modified=False)
        if (exchange.file_format or "XML") == "XML":
            orders = parse_orders_xml(raw)
        elif exchange.file_format == "CSV":
            orders = parse_orders_csv(raw)
        else:
            raise ValueError(f"Unsupported order file format: {exchange.file_format}")
        channel = frappe.get_doc("Ecommerce Channel", exchange.channel)
        result = import_orders(channel, orders)
        status = "Processed" if result["ok"] else "Failed"
        exchange.db_set(
            {
                "status": status,
                "row_count": len(orders),
                "processed_at": frappe.utils.now_datetime(),
                "result": json.dumps(result, ensure_ascii=False, default=str),
                "error": "" if result["ok"] else "One or more orders could not be imported; inspect integration logs.",
            },
            update_modified=False,
        )
        log_event(
            f"ecommerce:{channel.name}",
            "success" if result["ok"] else "error",
            "Order exchange file processed",
            direction="in",
            reference_doctype=exchange.doctype,
            reference_name=exchange.name,
            response_payload=result,
        )
        return result
    except Exception as exc:
        error = sanitize_text(str(exc) or "File import failed")[:2000]
        trace = frappe.get_traceback()
        exchange.db_set(
            {
                "status": "Failed",
                "processed_at": frappe.utils.now_datetime(),
                "result": json.dumps({"ok": False, "error": error}, ensure_ascii=False),
                "error": error,
            },
            update_modified=False,
        )
        log_event(
            f"ecommerce:{exchange.channel}",
            "error",
            "Order exchange file failed",
            direction="in",
            reference_doctype=exchange.doctype,
            reference_name=exchange.name,
            error_trace=trace,
        )
        return {"ok": False, "error": error}
