from __future__ import annotations

import frappe

from erpnext_ua.integrations.utils.operations import canonical_hash

UNIQUE_INDEX = "uniq_ecom_channel_external_id"


def execute() -> None:
    """Backfill the v0.5 mapping contract before enforcing its business key."""
    if not frappe.db.exists("DocType", "Ecommerce Item Mapping"):
        return
    required_columns = {
        "channel",
        "item",
        "external_id",
        "external_sku",
        "variant_sku",
        "sync_status",
        "mapping_key",
        "external_mapping_key",
    }
    if not required_columns.issubset(set(frappe.db.get_table_columns("Ecommerce Item Mapping"))):
        raise RuntimeError("Ecommerce Item Mapping schema is incomplete; post-model-sync patch cannot continue")

    seen: dict[tuple[str, str], str] = {}
    rows = frappe.get_all(
        "Ecommerce Item Mapping",
        fields=list(required_columns | {"name"}),
        order_by="creation asc",
        limit_page_length=1_000_000,
    )
    for row in rows:
        channel = str(row.channel or "").strip()
        item = str(row.item or "").strip()
        external_sku = str(row.external_sku or "").strip()
        external_id = str(row.external_id or external_sku or item).strip()
        variant_sku = str(row.variant_sku or external_sku or item).strip()
        if not channel or not item or not external_id:
            raise RuntimeError(f"Cannot migrate Ecommerce Item Mapping {row.name}: missing channel/item/external ID")
        business_key = (channel, external_id)
        if business_key in seen and seen[business_key] != row.name:
            raise RuntimeError(
                "Duplicate ecommerce mapping business key requires manual reconciliation: "
                f"{channel}/{external_id} ({seen[business_key]}, {row.name})"
            )
        seen[business_key] = row.name
        values = {
            "external_id": external_id,
            "variant_sku": variant_sku,
            "sync_status": row.sync_status or "Pending",
            "mapping_key": canonical_hash({"channel": channel, "item": item}),
            "external_mapping_key": canonical_hash(
                {"channel": channel, "external_id": external_id}
            ),
        }
        frappe.db.set_value(
            "Ecommerce Item Mapping",
            row.name,
            values,
            update_modified=False,
        )

    table_name = "tabEcommerce Item Mapping"
    if not frappe.db.has_index(table_name, UNIQUE_INDEX):
        frappe.db.add_unique(
            "Ecommerce Item Mapping",
            ["channel", "external_id"],
            constraint_name=UNIQUE_INDEX,
        )
