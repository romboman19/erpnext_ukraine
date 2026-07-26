from __future__ import annotations

import math

import frappe

from ukrainian_integrations.utils.logger import log_event
from ukrainian_integrations.utils.operations import canonical_hash


def import_orders(channel, orders: list[dict]) -> dict:
    created = 0
    skipped = 0
    failed = 0
    for order in orders:
        frappe.db.savepoint("ecommerce_order_import")
        try:
            outcome = _import_order(channel, order)
        except Exception:
            frappe.db.rollback(save_point="ecommerce_order_import")
            failed += 1
            log_event(
                f"ecommerce:{channel.name}",
                "error",
                f"Order import failed: {order.get('external_id') or 'missing-id'}",
                direction="in",
                error_trace=frappe.get_traceback(),
            )
            continue
        created += int(outcome == "created")
        skipped += int(outcome == "skipped")
    return {
        "ok": failed == 0,
        "received": len(orders),
        "created": created,
        "skipped": skipped,
        "failed": failed,
    }


def import_customers(channel, customers: list[dict]) -> dict:
    created = 0
    updated = 0
    failed = 0
    for customer in customers:
        frappe.db.savepoint("ecommerce_customer_import")
        try:
            _, outcome = _get_or_create_customer(channel, customer)
        except Exception:
            frappe.db.rollback(save_point="ecommerce_customer_import")
            failed += 1
            log_event(
                f"ecommerce:{channel.name}",
                "error",
                f"Customer import failed: {customer.get('external_id') or 'missing-id'}",
                direction="in",
                error_trace=frappe.get_traceback(),
            )
            continue
        created += int(outcome == "created")
        updated += int(outcome == "updated")
    return {"ok": failed == 0, "received": len(customers), "created": created, "updated": updated, "failed": failed}


def _import_order(channel, order: dict) -> str:
    external_id = str(order.get("external_id") or "").strip()
    if not external_id:
        raise ValueError("External order ID is required")
    if len(external_id) > 140:
        raise ValueError("External order ID is too long")
    order_key = _external_key("order", channel.name, external_id)
    if frappe.db.exists("Sales Order", {"ua_external_order_key": order_key}):
        return "skipped"

    currency = str(order.get("currency") or channel.currency or "UAH").strip().upper()
    expected_currency = str(channel.currency or "UAH").strip().upper()
    if currency != expected_currency:
        raise ValueError(f"Order currency {currency} does not match channel currency {expected_currency}")
    items = _order_items(channel, order.get("items") or [])
    customer, _ = _get_or_create_customer(channel, order.get("customer") or {})

    doc = frappe.new_doc("Sales Order")
    doc.company = channel.company
    doc.customer = customer
    doc.po_no = str(order.get("number") or external_id)[:64]
    doc.ua_external_order_key = order_key
    doc.ua_ecommerce_channel = channel.name
    doc.ua_external_order_id = external_id
    doc.currency = expected_currency
    doc.delivery_date = frappe.utils.nowdate()
    delivery = order.get("delivery") or {}
    notes = [f"E-commerce channel: {channel.name}", f"External order: {external_id}"]
    if order.get("status") not in (None, ""):
        notes.append(f"External status: {order['status']}")
    if order.get("paid") not in (None, ""):
        notes.append(f"External paid flag: {order['paid']}")
    if delivery.get("city") or delivery.get("address"):
        notes.append(f"Delivery: {delivery.get('city') or ''} {delivery.get('address') or ''}".strip())
    if order.get("comment"):
        notes.append(f"Customer comment: {order['comment']}")
    doc.remarks = "\n".join(notes)[:1000]
    for item in items:
        doc.append("items", item)
    try:
        doc.insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        if frappe.db.exists("Sales Order", {"ua_external_order_key": order_key}):
            return "skipped"
        raise
    return "created"


def _order_items(channel, source_items: list[dict]) -> list[dict]:
    if not isinstance(source_items, list) or not source_items:
        raise ValueError("Order contains no product rows")
    rows = []
    for item in source_items:
        sku = str(item.get("sku") or "").strip()
        if not sku:
            raise ValueError("Order product has no SKU")
        item_code = frappe.db.get_value(
            "Ecommerce Item Mapping",
            {"channel": channel.name, "external_sku": sku, "enabled": 1},
            "item",
        )
        if not item_code and not int(channel.export_only_mapped_items or 0) and frappe.db.exists("Item", sku):
            item_code = sku
        if not item_code:
            raise ValueError(f"External SKU is not mapped to an ERPNext Item: {sku}")
        try:
            quantity = float(item.get("quantity") or 0)
            price = float(item.get("price") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"Order product has invalid quantity or price: {sku}") from None
        if not math.isfinite(quantity) or not math.isfinite(price) or quantity <= 0 or price < 0:
            raise ValueError(f"Order product has invalid quantity or price: {sku}")
        rows.append({"item_code": item_code, "qty": quantity, "rate": price})
    return rows


def _get_or_create_customer(channel, customer: dict) -> tuple[str, str]:
    external_id = str(customer.get("external_id") or "").strip()
    phone = _normalize_phone(str(customer.get("phone") or ""))
    email = _valid_email(str(customer.get("email") or ""))
    identity_hash = canonical_hash({"phone": phone, "email": email})
    identity = external_id or identity_hash
    mapping_key = canonical_hash({"channel": channel.name, "identity": identity})
    existing = frappe.db.get_value(
        "Ecommerce Customer Mapping",
        {"mapping_key": mapping_key},
        "customer",
    )
    if existing:
        values = {}
        current = frappe.db.get_value("Customer", existing, ["mobile_no", "email_id"], as_dict=True) or {}
        if phone and not current.get("mobile_no"):
            values["mobile_no"] = phone
        if email and not current.get("email_id"):
            values["email_id"] = email
        if values:
            frappe.db.set_value("Customer", existing, values, update_modified=False)
            return existing, "updated"
        return existing, "existing"

    candidates = set()
    if email:
        candidates.update(frappe.get_all("Customer", filters={"email_id": email}, pluck="name", limit=2))
    if phone:
        candidates.update(frappe.get_all("Customer", filters={"mobile_no": phone}, pluck="name", limit=2))
    outcome = "existing"
    if len(candidates) > 1:
        raise ValueError("Customer phone and email match different ERPNext customers")
    if len(candidates) == 1:
        existing = next(iter(candidates))
    else:
        customer_name = str(customer.get("name") or phone or email or f"{channel.name} Customer").strip()[:140]
        doc = frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": customer_name,
                "customer_group": channel.customer_group,
                "territory": channel.territory,
                "mobile_no": phone or None,
                "email_id": email or None,
            }
        )
        doc.insert(ignore_permissions=True)
        existing = doc.name
        outcome = "created"

    mapping = frappe.get_doc(
        {
            "doctype": "Ecommerce Customer Mapping",
            "channel": channel.name,
            "customer": existing,
            "external_customer_id": external_id,
            "identity_hash": identity_hash,
            "mapping_key": mapping_key,
        }
    )
    try:
        mapping.insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        mapped_customer = frappe.db.get_value(
            "Ecommerce Customer Mapping",
            {"mapping_key": mapping_key},
            "customer",
        )
        if mapped_customer:
            return mapped_customer, "existing"
        raise
    return existing, outcome


def _external_key(entity: str, channel: str, external_id: str) -> str:
    digest = canonical_hash({"channel": channel, "external_id": external_id})
    prefix = "ecom:o" if entity == "order" else "ecom:c"
    return f"{prefix}:{digest}"


def _normalize_phone(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = "38" + digits
    return f"+{digits}" if 10 <= len(digits) <= 15 else ""


def _valid_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 254 or email.count("@") != 1:
        return ""
    local, domain = email.split("@", 1)
    return email if local and "." in domain and not domain.startswith(".") and not domain.endswith(".") else ""
