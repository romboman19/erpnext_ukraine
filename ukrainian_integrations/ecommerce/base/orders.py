from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import frappe

from ukrainian_integrations.ecommerce.base.logging import append_sync_log
from ukrainian_integrations.utils.operations import (
    canonical_hash,
    load_response,
    mark_operation,
    require_new_or_return_success,
    reserve_operation,
)


@dataclass(frozen=True)
class OrderCustomer:
    phone: str
    name: str
    email: str = ""


@dataclass(frozen=True)
class OrderShippingAddress:
    city: str = ""
    address: str = ""


@dataclass(frozen=True)
class OrderPayment:
    payment_type: str = ""
    amount: float = 0
    currency: str = "UAH"
    paid: bool = False


@dataclass(frozen=True)
class OrderItem:
    external_id: str
    variant_sku: str
    quantity: float
    price: float
    name: str = ""


@dataclass(frozen=True)
class NormalizedOrder:
    channel_order_id: str
    channel_status: str
    currency: str
    customer: OrderCustomer
    shipping_address: OrderShippingAddress
    payment: OrderPayment
    items: tuple[OrderItem, ...]
    order_number: str = ""
    comment: str = ""


def normalize_order(raw_order: dict, *, default_currency: str = "UAH") -> NormalizedOrder:
    if not isinstance(raw_order, dict):
        raise ValueError("Ecommerce order payload must be an object")
    order_id = str(raw_order.get("channel_order_id") or raw_order.get("external_id") or "").strip()
    if not order_id or len(order_id) > 140:
        raise ValueError("Channel order ID is required and must not exceed 140 characters")
    status = str(raw_order.get("channel_status") or raw_order.get("status") or "").strip()
    if not status:
        raise ValueError("Channel order status is required")
    currency = str(raw_order.get("currency") or default_currency or "UAH").strip().upper()
    customer = raw_order.get("customer") or {}
    phone = normalize_phone(str(customer.get("phone") or raw_order.get("phone") or ""))
    if not phone:
        raise ValueError("Ecommerce customer requires a valid mobile phone")
    customer_name = str(customer.get("name") or phone).strip()[:140]
    email = valid_email(str(customer.get("email") or ""))
    shipping = raw_order.get("shipping_address") or raw_order.get("delivery") or {}
    payment_data = raw_order.get("payment") or {}
    payment_amount = _finite_number(payment_data.get("amount") or 0, "payment amount")
    if payment_amount < 0:
        raise ValueError("Ecommerce payment amount cannot be negative")
    items = tuple(_normalize_item(item) for item in (raw_order.get("items") or []))
    if not items:
        raise ValueError("Ecommerce order contains no product rows")
    return NormalizedOrder(
        channel_order_id=order_id,
        channel_status=status,
        currency=currency,
        customer=OrderCustomer(phone=phone, name=customer_name, email=email),
        shipping_address=OrderShippingAddress(
            city=str(shipping.get("city") or "").strip(),
            address=str(shipping.get("address") or "").strip(),
        ),
        payment=OrderPayment(
            payment_type=str(payment_data.get("type") or payment_data.get("payment_type") or "").strip(),
            amount=payment_amount,
            currency=str(payment_data.get("currency") or currency).strip().upper(),
            paid=_as_bool(payment_data.get("paid", raw_order.get("paid", False))),
        ),
        items=items,
        order_number=str(raw_order.get("order_number") or raw_order.get("number") or order_id).strip()[:64],
        comment=str(raw_order.get("comment") or "").strip()[:1000],
    )


def intake(channel: Any, raw_order: dict) -> dict:
    """Idempotently turn one normalized channel order into standard ERP documents."""
    channel_key = _channel_key(channel)
    order = normalize_order(raw_order, default_currency=_value(channel, "currency", "UAH"))
    idempotency_key = order_idempotency_key(channel_key, order.channel_order_id)
    action_row = _status_action(channel, order.channel_status)
    frappe.db.savepoint("ecommerce_order_intake")
    try:
        result = _intake_order(
            channel,
            channel_key,
            order,
            idempotency_key,
            action_row,
        )
        append_sync_log(
            channel=channel_key,
            entity="Orders",
            direction="Import",
            method=_value(channel, "_active_order_method", "File") or "File",
            status="Success",
            idempotency_key=idempotency_key,
            records_ok=1,
            message=f"Order {order.channel_order_id}: {result['outcome']}",
            payload_ref=result.get("sales_order") or result.get("sales_invoice") or "",
        )
        return result
    except Exception as exc:
        frappe.db.rollback(save_point="ecommerce_order_intake")
        append_sync_log(
            channel=channel_key,
            entity="Orders",
            direction="Import",
            method=_value(channel, "_active_order_method", "File") or "File",
            status="Failed",
            idempotency_key=idempotency_key,
            records_failed=1,
            message=f"Order {order.channel_order_id} failed: {exc}",
        )
        raise


def order_idempotency_key(channel_key: str, channel_order_id: str) -> str:
    return f"ecom:o:{canonical_hash({'channel': channel_key, 'channel_order_id': channel_order_id})}"


def normalize_phone(value: str) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = "38" + digits
    return f"+{digits}" if 10 <= len(digits) <= 15 else ""


def valid_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 254 or email.count("@") != 1:
        return ""
    local, domain = email.split("@", 1)
    return email if local and "." in domain and not domain.startswith(".") and not domain.endswith(".") else ""


def _normalize_item(item: dict) -> OrderItem:
    if not isinstance(item, dict):
        raise ValueError("Ecommerce order contains an invalid product row")
    external_id = str(item.get("external_id") or item.get("sku") or "").strip()
    variant_sku = str(item.get("variant_sku") or item.get("sku") or "").strip()
    if not external_id:
        raise ValueError("Ecommerce order product requires an external ID")
    quantity = _finite_number(item.get("quantity"), "product quantity")
    price = _finite_number(item.get("price"), "product price")
    if quantity <= 0 or price < 0:
        raise ValueError(f"Invalid quantity or price for ecommerce product {external_id}")
    return OrderItem(
        external_id=external_id,
        variant_sku=variant_sku,
        quantity=quantity,
        price=price,
        name=str(item.get("name") or "").strip(),
    )


def _intake_order(
    channel: Any,
    channel_key: str,
    order: NormalizedOrder,
    idempotency_key: str,
    action_row: Any,
) -> dict:
    existing = _existing_documents(idempotency_key)
    action = str(_value(action_row, "erp_action", "") or "")
    if existing and action == "Update Status":
        _update_existing_status(existing, order.channel_status)
        return {"ok": True, "outcome": "updated", **existing}
    if existing:
        return {"ok": True, "outcome": "found", **existing}
    if action == "Ignore":
        return {"ok": True, "outcome": "ignored"}
    if action == "Update Status":
        raise ValueError("Update Status requires an existing ecommerce order")

    expected_currency = str(_value(channel, "currency", "UAH") or "UAH").strip().upper()
    if order.currency != expected_currency or order.payment.currency != expected_currency:
        raise ValueError("Order/payment currency does not match the channel currency")
    item_rows = _resolve_items(channel_key, channel, order.items)
    customer = _resolve_customer(channel, order.customer)
    if action == "Create Sales Order":
        sales_order = _create_sales_order(
            channel,
            channel_key,
            order,
            customer,
            item_rows,
            idempotency_key,
            action_row,
        )
        return {"ok": True, "outcome": "created", "sales_order": sales_order.name}
    if action == "Create Sales Invoice":
        sales_invoice = _create_direct_sales_invoice(
            channel,
            channel_key,
            order,
            customer,
            item_rows,
            idempotency_key,
        )
        return {"ok": True, "outcome": "created", "sales_invoice": sales_invoice.name}
    if action == "Create SO+SI+Payment":
        sales_order = _create_sales_order(
            channel,
            channel_key,
            order,
            customer,
            item_rows,
            idempotency_key,
            action_row,
            force_submit=True,
        )
        sales_invoice = _create_invoice_from_order(sales_order, idempotency_key, channel_key, order)
        payment_entry = _create_payment(channel, channel_key, order, sales_invoice)
        return {
            "ok": True,
            "outcome": "created",
            "sales_order": sales_order.name,
            "sales_invoice": sales_invoice.name,
            "payment_entry": payment_entry,
        }
    raise ValueError(f"Unsupported ecommerce ERP action: {action}")


def _existing_documents(idempotency_key: str) -> dict:
    sales_order = frappe.db.get_value("Sales Order", {"ua_external_order_key": idempotency_key}, "name")
    if sales_order:
        result = {"sales_order": sales_order}
        sales_invoice = frappe.db.get_value(
            "Sales Invoice",
            {"ua_external_order_key": idempotency_key},
            "name",
        )
        if sales_invoice:
            result["sales_invoice"] = sales_invoice
        return result
    sales_invoice = frappe.db.get_value(
        "Sales Invoice",
        {"ua_external_order_key": idempotency_key},
        "name",
    )
    if sales_invoice:
        return {"sales_invoice": sales_invoice}
    sync_log = frappe.db.get_value(
        "Ecommerce Sync Log",
        {"idempotency_key": idempotency_key, "status": "Success"},
        "name",
    )
    return {"sync_log": sync_log} if sync_log else {}


def _update_existing_status(existing: dict, channel_status: str) -> None:
    for doctype, key in (("Sales Order", "sales_order"), ("Sales Invoice", "sales_invoice")):
        if existing.get(key):
            frappe.db.set_value(
                doctype,
                existing[key],
                "ua_ecommerce_status",
                channel_status,
                update_modified=False,
            )


def _status_action(channel: Any, channel_status: str):
    matches = [
        row
        for row in (_value(channel, "order_status_map", []) or [])
        if str(_value(row, "channel_status", "") or "").strip() == channel_status
    ]
    if len(matches) != 1:
        raise ValueError(f"Exactly one ERP action must be configured for channel status: {channel_status}")
    return matches[0]


def _resolve_items(channel_key: str, channel: Any, source_items: tuple[OrderItem, ...]) -> list[dict]:
    warehouse = next(
        (
            _value(row, "warehouse")
            for row in (_value(channel, "warehouses", []) or [])
            if int(_value(row, "enabled", 0) or 0) and _value(row, "warehouse")
        ),
        None,
    )
    rows = []
    for source in source_items:
        filters = {"channel": channel_key, "external_id": source.external_id}
        candidates = frappe.get_all(
            "Ecommerce Item Mapping",
            filters=filters,
            fields=["item", "variant_sku", "sync_status"],
            limit=2,
        )
        if source.variant_sku:
            exact = [row for row in candidates if str(row.variant_sku or "") == source.variant_sku]
            if exact:
                candidates = exact
        if len(candidates) != 1 or candidates[0].sync_status == "Disabled":
            raise ValueError(
                f"External product mapping is missing or ambiguous: {source.external_id}/{source.variant_sku}"
            )
        item_code = candidates[0].item
        item_state = frappe.db.get_value("Item", item_code, ["name", "disabled"], as_dict=True)
        if not item_state or int(item_state.disabled or 0):
            raise ValueError(f"Mapped ERP Item is missing or disabled: {item_code}")
        row = {
            "item_code": item_code,
            "qty": source.quantity,
            "rate": source.price,
            "delivery_date": frappe.utils.nowdate(),
        }
        if warehouse:
            row["warehouse"] = warehouse
        rows.append(row)
    return rows


def _resolve_customer(channel: Any, customer: OrderCustomer) -> str:
    candidate_names = set(
        frappe.get_all(
            "Customer",
            filters={"mobile_no": ["in", _phone_variants(customer.phone)]},
            pluck="name",
            limit=3,
        )
    )
    contacts = frappe.get_all(
        "Contact",
        or_filters={
            "mobile_no": ["in", _phone_variants(customer.phone)],
            "phone": ["in", _phone_variants(customer.phone)],
        },
        pluck="name",
        limit=3,
    )
    if contacts:
        candidate_names.update(
            frappe.get_all(
                "Dynamic Link",
                filters={
                    "parenttype": "Contact",
                    "parent": ["in", contacts],
                    "link_doctype": "Customer",
                },
                pluck="link_name",
                limit=4,
            )
        )
    if len(candidate_names) > 1:
        raise ValueError("Customer phone matches multiple ERPNext customers")
    if candidate_names:
        return next(iter(candidate_names))
    customer_group = _value(channel, "default_customer_group")
    territory = _value(channel, "default_territory")
    if not customer_group or not territory:
        raise ValueError("Channel requires a default Customer Group and Territory")
    doc = frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": customer.name,
            "customer_group": customer_group,
            "territory": territory,
            "mobile_no": customer.phone,
            "email_id": customer.email or None,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name


def _create_sales_order(
    channel: Any,
    channel_key: str,
    order: NormalizedOrder,
    customer: str,
    item_rows: list[dict],
    idempotency_key: str,
    action_row: Any,
    *,
    force_submit: bool = False,
):
    reserve_stock = bool(int(_value(action_row, "reserve_stock", 0) or 0))
    reserve_days = int(_value(action_row, "reserve_days", 0) or 0) if reserve_stock else 0
    doc = frappe.new_doc("Sales Order")
    doc.company = _required(channel, "company")
    doc.customer = customer
    doc.currency = order.currency
    doc.po_no = order.order_number
    doc.delivery_date = frappe.utils.nowdate()
    doc.ua_external_order_key = idempotency_key
    doc.ua_ecommerce_channel = channel_key
    doc.ua_external_order_id = order.channel_order_id
    doc.ua_ecommerce_status = order.channel_status
    doc.ua_ecommerce_reserve_until = frappe.utils.add_days(date.today(), reserve_days) if reserve_days else None
    doc.reserve_stock = int(reserve_stock)
    doc.remarks = _order_remarks(order)
    for row in item_rows:
        doc.append("items", row)
    try:
        doc.insert(ignore_permissions=True)
        if reserve_stock or force_submit:
            doc.submit()
    except frappe.DuplicateEntryError:
        existing = frappe.db.get_value("Sales Order", {"ua_external_order_key": idempotency_key}, "name")
        if existing:
            return frappe.get_doc("Sales Order", existing)
        raise
    return doc


def _create_direct_sales_invoice(
    channel: Any,
    channel_key: str,
    order: NormalizedOrder,
    customer: str,
    item_rows: list[dict],
    idempotency_key: str,
):
    doc = frappe.new_doc("Sales Invoice")
    doc.company = _required(channel, "company")
    doc.customer = customer
    doc.currency = order.currency
    doc.due_date = frappe.utils.nowdate()
    doc.ua_external_order_key = idempotency_key
    doc.ua_ecommerce_channel = channel_key
    doc.ua_external_order_id = order.channel_order_id
    doc.ua_ecommerce_status = order.channel_status
    doc.remarks = _order_remarks(order)
    for row in item_rows:
        doc.append("items", {key: value for key, value in row.items() if key != "delivery_date"})
    try:
        doc.insert(ignore_permissions=True)
        doc.submit()
    except frappe.DuplicateEntryError:
        existing = frappe.db.get_value("Sales Invoice", {"ua_external_order_key": idempotency_key}, "name")
        if existing:
            return frappe.get_doc("Sales Invoice", existing)
        raise
    return doc


def _create_invoice_from_order(sales_order, idempotency_key: str, channel_key: str, order: NormalizedOrder):
    from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

    doc = frappe.get_doc(make_sales_invoice(sales_order.name))
    doc.ua_external_order_key = idempotency_key
    doc.ua_ecommerce_channel = channel_key
    doc.ua_external_order_id = order.channel_order_id
    doc.ua_ecommerce_status = order.channel_status
    doc.remarks = _order_remarks(order)
    doc.insert(ignore_permissions=True)
    doc.submit()
    return doc


def _create_payment(channel: Any, channel_key: str, order: NormalizedOrder, sales_invoice) -> str:
    if not order.payment.paid or order.payment.amount <= 0:
        raise ValueError("Create SO+SI+Payment requires a positive paid order amount")
    if abs(float(sales_invoice.grand_total or 0) - order.payment.amount) > 0.01:
        raise ValueError("Paid order amount does not match the Sales Invoice grand total")
    routes = [
        row
        for row in (_value(channel, "payment_routes", []) or [])
        if str(_value(row, "channel_payment_type", "") or "").strip() == order.payment.payment_type
    ]
    if len(routes) != 1:
        raise ValueError(f"Exactly one payment route is required for {order.payment.payment_type}")
    route = routes[0]
    account = _required(route, "paid_to_account")
    account_state = frappe.db.get_value(
        "Account",
        account,
        ["company", "account_currency", "is_group"],
        as_dict=True,
    )
    if (
        not account_state
        or int(account_state.is_group or 0)
        or account_state.company != _required(channel, "company")
        or str(account_state.account_currency or "").upper() != order.payment.currency
    ):
        raise ValueError("Paid To Account must be a company ledger account in the order currency")
    request_payload = {
        "channel": channel_key,
        "channel_order_id": order.channel_order_id,
        "sales_invoice": sales_invoice.name,
        "amount": order.payment.amount,
        "currency": order.payment.currency,
        "mode_of_payment": _value(route, "mode_of_payment"),
        "paid_to_account": account,
    }
    payment_key = f"ecom:p:{canonical_hash({'channel': channel_key, 'channel_order_id': order.channel_order_id})}"
    reservation = reserve_operation(
        idempotency_key=payment_key,
        integration="ecommerce_payment",
        operation_type="create_payment_entry",
        request_payload=request_payload,
        reference_doctype="Sales Invoice",
        reference_name=sales_invoice.name,
        durable=False,
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return str(cached.get("payment_entry") or "")
    try:
        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

        payment = get_payment_entry(
            "Sales Invoice",
            sales_invoice.name,
            party_amount=order.payment.amount,
        )
        payment.mode_of_payment = _required(route, "mode_of_payment")
        payment.paid_to = account
        payment.paid_amount = order.payment.amount
        payment.received_amount = order.payment.amount
        payment.insert(ignore_permissions=True)
        payment.submit()
    except Exception as exc:
        mark_operation(reservation.doc, "failed", error=str(exc), durable=False)
        raise
    mark_operation(
        reservation.doc,
        "reconciled",
        external_id=payment.name,
        response_payload={"payment_entry": payment.name},
        durable=False,
    )
    return str(load_response(reservation.doc).get("payment_entry") or payment.name)


def _order_remarks(order: NormalizedOrder) -> str:
    values = [
        f"External order: {order.channel_order_id}",
        f"External status: {order.channel_status}",
        f"Delivery: {order.shipping_address.city} {order.shipping_address.address}".strip(),
    ]
    if order.comment:
        values.append(f"Customer comment: {order.comment}")
    return "\n".join(value for value in values if value and not value.endswith(":"))[:1000]


def _channel_key(channel: Any) -> str:
    doctype = str(_value(channel, "doctype", "") or "").strip()
    name = str(_value(channel, "name", "") or "").strip()
    if not doctype or not name:
        raise ValueError("Ecommerce channel settings require a DocType and name")
    return f"{doctype}:{name}"


def _phone_variants(phone: str) -> list[str]:
    digits = phone.lstrip("+")
    values = {phone, digits}
    if digits.startswith("38") and len(digits) == 12:
        values.add("0" + digits[2:])
    return sorted(values)


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        raise ValueError(f"Ecommerce {label} is invalid") from None
    if not math.isfinite(number):
        raise ValueError(f"Ecommerce {label} is invalid")
    return number


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value == 1
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "y", "paid"}:
        return True
    if normalized in {"", "0", "false", "no", "n", "unpaid"}:
        return False
    raise ValueError("Ecommerce paid flag is invalid")


def _required(source: Any, fieldname: str):
    result = _value(source, fieldname)
    if result in (None, ""):
        raise ValueError(f"Ecommerce channel requires {fieldname}")
    return result


def _value(source: Any, key: str, default=None):
    if isinstance(source, dict):
        return source.get(key, default)
    getter = getattr(source, "get", None)
    return getter(key, default) if callable(getter) else getattr(source, key, default)
