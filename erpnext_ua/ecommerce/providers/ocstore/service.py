from __future__ import annotations

import hashlib
import json

import frappe
from frappe import _

from erpnext_ua.ecommerce.base import orders
from erpnext_ua.ecommerce.base.logging import append_sync_log
from erpnext_ua.ecommerce.base.mapping import serialized_payload_hash
from erpnext_ua.ecommerce.base.serializers import get_serializer
from erpnext_ua.ecommerce.base.transport import (
    AmbiguousTransportError,
    FileDeliveryTransport,
)
from erpnext_ua.ecommerce.providers.ocstore.catalog import collect_records
from erpnext_ua.ecommerce.providers.ocstore.xml_orders import parse_order_file
from erpnext_ua.integrations.utils.logger import sanitize_text
from erpnext_ua.integrations.utils.operations import canonical_hash

EXPORT_ENTITIES = {"Products", "Prices", "Stock", "Photos"}


def export_bundle(
    settings_name: str,
    *,
    force: bool = False,
    entities: list[str] | None = None,
) -> dict:
    settings = _settings(settings_name)
    channel_key = _channel_key(settings)
    requested = set(entities or EXPORT_ENTITIES)
    if not requested.issubset(EXPORT_ENTITIES):
        raise ValueError("Unsupported ocStore export entity selection")
    all_configs = _active_configs(settings, EXPORT_ENTITIES)
    configs = [row for row in all_configs if row.entity in requested]
    if not configs:
        frappe.throw(
            _(
                "Enable at least one ocStore export entity (Products, Prices, Stock or Photos), "
                "save the settings and try again."
            )
        )
    uploaded_files = []
    records = []
    mapping_by_item = {}
    try:
        layouts = {row.entity: frappe.get_doc("Ecommerce File Layout", row.file_layout) for row in configs}
        include_photos = any(_layout_uses_photos(layout) for layout in layouts.values())
        records, mapping_by_item, photo_assets = collect_records(
            settings,
            include_photos=include_photos,
        )
        hashes = _record_entity_hashes(records, configs, layouts)
        states = {
            record["item"]: _load_hash_state(mapping_by_item[record["item"]])
            for record in records
        }
        changed_by_entity = {
            config.entity: [
                record
                for record in records
                if states[record["item"]].get(config.entity)
                != hashes[record["item"]][config.entity]
            ]
            for config in configs
        }
        publish_configs = [
            config for config in configs if force or changed_by_entity[config.entity]
        ]
        if not publish_configs:
            now = frappe.utils.now_datetime()
            _mark_entity_runs(configs, now)
            append_sync_log(
                channel=channel_key,
                entity=configs[0].entity,
                direction="Export",
                method="File",
                status="Success",
                idempotency_key=f"ecom:x:{canonical_hash({'channel': channel_key, 'entities': sorted(requested), 'state': 'unchanged'})}",
                records_ok=len(records),
                message="Skipped unchanged ocStore XML bundle",
            )
            frappe.db.commit()
            return {"ok": True, "skipped": True, "reason": "unchanged", "records": len(records)}

        if photo_assets and any(
            _layout_uses_photos(layouts[config.entity]) for config in publish_configs
        ):
            photo_transport = _transport(settings.photo_ftp_profile)
            for asset in photo_assets:
                photo_transport.upload(
                    asset.remote_name,
                    asset.content,
                    idempotency_key=asset.idempotency_key,
                )

        exchange_transport = _transport(settings.ftp_profile)
        for config in publish_configs:
            serializer = get_serializer("XML")
            payload = serializer.serialize(records, layouts[config.entity])
            digest = serialized_payload_hash(payload)
            operation_key = f"ecom:x:{canonical_hash({'channel': channel_key, 'entity': config.entity, 'sha256': digest})}"
            remote_name = f"{settings.export_file_prefix}-{config.entity.lower()}.xml"
            exchange_transport.publish(remote_name, payload, idempotency_key=operation_key)
            uploaded_files.append(remote_name)
            append_sync_log(
                channel=channel_key,
                entity=config.entity,
                direction="Export",
                method="File",
                status="Success",
                idempotency_key=operation_key,
                records_ok=len(records),
                message=f"Published ocStore {config.entity} XML",
                payload_ref=remote_name,
            )

        now = frappe.utils.now_datetime()
        active_entities = {config.entity for config in all_configs}
        for record in records:
            state = {
                entity: digest
                for entity, digest in states[record["item"]].items()
                if entity in active_entities
            }
            for config in configs:
                state[config.entity] = hashes[record["item"]][config.entity]
            frappe.db.set_value(
                "Ecommerce Item Mapping",
                mapping_by_item[record["item"]].name,
                {
                    "last_export_hash": _combined_hash(state),
                    "export_hash_state": json.dumps(
                        state,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "sync_status": "Synced",
                    "last_synced_at": now,
                },
                update_modified=False,
            )
        _mark_entity_runs(configs, now)
        frappe.db.set_value(
            "OcStore Settings",
            settings.name,
            {"last_export_at": now, "last_error": ""},
            update_modified=False,
        )
        # External files are already visible; make their hashes/logs durable now.
        frappe.db.commit()
        return {
            "ok": True,
            "records": len(records),
            "changed": len(
                {
                    record["item"]
                    for changed in changed_by_entity.values()
                    for record in changed
                }
            ),
            "files": uploaded_files,
            "photos": len(photo_assets),
        }
    except Exception as exc:
        status = "Unknown" if isinstance(exc, AmbiguousTransportError) else (
            "Partial" if uploaded_files else "Failed"
        )
        now = frappe.utils.now_datetime()
        message = sanitize_text(str(exc) or "ocStore export failed")[:1000]
        for mapping in mapping_by_item.values():
            frappe.db.set_value(
                "Ecommerce Item Mapping",
                mapping.name,
                "sync_status",
                "Unknown" if status == "Unknown" else "Failed",
                update_modified=False,
            )
        append_sync_log(
            channel=channel_key,
            entity=configs[0].entity,
            direction="Export",
            method="File",
            status=status,
            idempotency_key=f"ecom:x:{canonical_hash({'channel': channel_key, 'files': uploaded_files})}",
            records_failed=max(1, len(records)),
            message=message,
            payload_ref=",".join(uploaded_files),
        )
        frappe.db.set_value(
            "OcStore Settings",
            settings.name,
            "last_error",
            message,
            update_modified=False,
        )
        _mark_entity_runs(configs, now)
        frappe.db.commit()
        raise


def import_order_files(settings_name: str) -> dict:
    settings = _settings(settings_name)
    config = _active_order_config(settings)
    layout = frappe.get_doc("Ecommerce File Layout", config.file_layout)
    transport = _transport(settings.ftp_profile)
    channel_key = _channel_key(settings)
    try:
        files = [
            name
            for name in transport.list_files(suffix=".xml")
            if name.startswith(settings.orders_file_prefix)
        ][: int(settings.max_order_files_per_run or 20)]
    except Exception as exc:
        status = "Unknown" if isinstance(exc, AmbiguousTransportError) else "Failed"
        message = sanitize_text(str(exc) or "Cannot list ocStore order files")[:1000]
        now = frappe.utils.now_datetime()
        _mark_entity_runs([config], now)
        append_sync_log(
            channel=channel_key,
            entity="Orders",
            direction="Import",
            method="File",
            status=status,
            idempotency_key=f"ecom:f:{canonical_hash({'channel': channel_key, 'action': 'list'})}",
            records_failed=1,
            message=message,
        )
        frappe.db.set_value(
            "OcStore Settings",
            settings.name,
            "last_error",
            message,
            update_modified=False,
        )
        frappe.db.commit()
        raise
    if not files:
        now = frappe.utils.now_datetime()
        _mark_entity_runs([config], now)
        append_sync_log(
            channel=channel_key,
            entity="Orders",
            direction="Import",
            method="File",
            status="Success",
            idempotency_key=f"ecom:f:{canonical_hash({'channel': channel_key, 'state': 'empty'})}",
            message="No ocStore order files are waiting",
        )
        frappe.db.commit()
    results = []
    for remote_name in files:
        results.append(_process_order_file(settings, config, layout, transport, remote_name))
    ok = all(result["ok"] for result in results)
    return {
        "ok": ok,
        "files_seen": len(files),
        "files_processed": sum(int(result.get("deleted", False)) for result in results),
        "orders_ok": sum(int(result.get("orders_ok", 0)) for result in results),
        "files_failed": sum(int(not result["ok"]) for result in results),
        "results": results,
    }


def _process_order_file(settings, config, layout, transport, remote_name: str) -> dict:
    channel_key = _channel_key(settings)
    raw = b""
    order_rows: list[dict] = []
    file_key = f"ecom:f:{canonical_hash({'channel': channel_key, 'remote_name': remote_name})}"
    try:
        raw = transport.download(remote_name)
        digest = hashlib.sha256(raw).hexdigest()
        file_key = f"ecom:f:{canonical_hash({'channel': channel_key, 'remote_name': remote_name, 'sha256': digest})}"
        order_rows = parse_order_file(raw, layout)
        settings._active_order_method = "File"
        outcomes = []
        for order_row in order_rows:
            outcomes.append(orders.intake(settings, order_row))

        now = frappe.utils.now_datetime()
        frappe.db.set_value(
            "Ecommerce Sync Entity Config",
            config.name,
            "last_run_at",
            now,
            update_modified=False,
        )
        frappe.db.set_value(
            "OcStore Settings",
            settings.name,
            {"last_order_import_at": now, "last_error": ""},
            update_modified=False,
        )
        # Acceptance invariant: every ERP document and per-order Success log is
        # committed before the inbound file is deleted from FTP.
        frappe.db.commit()
    except Exception as exc:
        # Roll back the complete file batch, including earlier orders in it.
        frappe.db.rollback()
        message = sanitize_text(str(exc) or "ocStore order file failed")[:1000]
        append_sync_log(
            channel=channel_key,
            entity="Orders",
            direction="Import",
            method="File",
            status="Failed",
            idempotency_key=file_key,
            records_failed=max(1, len(order_rows)),
            message=f"Order file {remote_name} failed: {message}",
            payload_ref=remote_name,
        )
        frappe.db.set_value(
            "OcStore Settings",
            settings.name,
            "last_error",
            message,
            update_modified=False,
        )
        frappe.db.set_value(
            "Ecommerce Sync Entity Config",
            config.name,
            "last_run_at",
            frappe.utils.now_datetime(),
            update_modified=False,
        )
        # Persist the Failed audit record, but deliberately keep the FTP file.
        frappe.db.commit()
        return {"ok": False, "deleted": False, "file": remote_name, "error": message}

    try:
        deletion = transport.delete(remote_name)
    except AmbiguousTransportError as exc:
        message = sanitize_text(str(exc) or "ocStore order file deletion is unknown")[:1000]
        append_sync_log(
            channel=channel_key,
            entity="Orders",
            direction="Import",
            method="File",
            status="Unknown",
            idempotency_key=file_key,
            records_ok=len(order_rows),
            message=message,
            payload_ref=remote_name,
        )
        frappe.db.commit()
        return {
            "ok": False,
            "deleted": False,
            "file": remote_name,
            "orders_ok": len(order_rows),
            "error": message,
        }

    append_sync_log(
        channel=channel_key,
        entity="Orders",
        direction="Import",
        method="File",
        status="Success",
        idempotency_key=file_key,
        records_ok=len(order_rows),
        message=f"Imported and removed ocStore order file {remote_name}",
        payload_ref=remote_name,
    )
    frappe.db.commit()
    return {
        "ok": True,
        "deleted": bool(deletion.get("deleted")),
        "file": remote_name,
        "orders_ok": len(order_rows),
        "created": sum(int(result.get("outcome") == "created") for result in outcomes),
        "found": sum(int(result.get("outcome") == "found") for result in outcomes),
    }


def _record_entity_hashes(records: list[dict], configs: list, layouts: dict) -> dict[str, dict[str, str]]:
    result = {}
    for record in records:
        result[record["item"]] = {}
        for config in configs:
            payload = get_serializer("XML").serialize([record], layouts[config.entity])
            result[record["item"]][config.entity] = serialized_payload_hash(payload)
    return result


def _load_hash_state(mapping) -> dict[str, str]:
    try:
        state = json.loads(mapping.export_hash_state or "{}")
    except (TypeError, ValueError):
        raise ValueError(f"Invalid export hash state for Item mapping {mapping.name}") from None
    if not isinstance(state, dict):
        raise ValueError(f"Invalid export hash state for Item mapping {mapping.name}")
    return {
        str(entity): str(digest)
        for entity, digest in state.items()
        if isinstance(entity, str) and isinstance(digest, str)
    }


def _combined_hash(state: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for entity, payload_hash in sorted(state.items()):
        entity_bytes = entity.encode("utf-8")
        hash_bytes = payload_hash.encode("ascii")
        digest.update(len(entity_bytes).to_bytes(2, "big"))
        digest.update(entity_bytes)
        digest.update(hash_bytes)
    return digest.hexdigest()


def _layout_uses_photos(layout) -> bool:
    for field in layout.get("fields") or []:
        getter = getattr(field, "get", None)
        name = getter("erp_fieldname") if callable(getter) else getattr(field, "erp_fieldname", "")
        if str(name or "").strip() == "photo_urls":
            return True
    return False


def _active_configs(settings, entities: set[str]) -> list:
    return [
        row
        for row in (settings.get("sync_entities") or [])
        if row.entity in entities and int(row.enabled or 0) and row.method == "File"
    ]


def _active_order_config(settings):
    rows = _active_configs(settings, {"Orders"})
    if len(rows) != 1:
        frappe.throw(
            _(
                "Enable the ocStore Orders import entity with the File method, "
                "save the settings and try again."
            )
        )
    return rows[0]


def _mark_entity_runs(configs: list, timestamp) -> None:
    for config in configs:
        frappe.db.set_value(
            "Ecommerce Sync Entity Config",
            config.name,
            "last_run_at",
            timestamp,
            update_modified=False,
        )


def _settings(settings_name: str):
    doc = frappe.get_doc("OcStore Settings", settings_name)
    if not doc.name:
        raise ValueError("OcStore Settings document is required")
    return doc


def _transport(endpoint_name: str) -> FileDeliveryTransport:
    if not endpoint_name:
        raise ValueError("OcStore File Delivery Endpoint is required")
    return FileDeliveryTransport(frappe.get_doc("File Delivery Endpoint", endpoint_name))


def _channel_key(settings) -> str:
    return f"OcStore Settings:{settings.name}"
