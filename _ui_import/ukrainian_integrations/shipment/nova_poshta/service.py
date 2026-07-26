from __future__ import annotations

import json
import math
import re
from urllib.parse import quote

import frappe
import requests
from frappe import _

from ukrainian_integrations.utils.operations import (
    load_response,
    mark_operation,
    require_new_or_return_success,
    reserve_operation,
)
from ukrainian_integrations.utils.security import (
    SALES_MANAGER_ROLES,
    SALES_ROLES,
    SYSTEM_ROLES,
    permitted_doc,
    require_roles,
)

from .api import NovaPoshtaClient


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _normalize_phone(phone: str) -> str:
    p = "".join(ch for ch in (phone or "") if ch.isdigit())
    if p.startswith("0"):
        p = "38" + p
    if p.startswith("380") and len(p) == 12:
        return p
    return p


def _today_np() -> str:
    return frappe.utils.now_datetime().strftime("%d.%m.%Y")


def _bounded_number(value, *, default: float, minimum: float, maximum: float, label: str) -> float:
    try:
        number = float(default if value is None else value)
    except (TypeError, ValueError):
        frappe.throw(_("{0} must be numeric").format(label))
    if not math.isfinite(number) or number < minimum or number > maximum:
        frappe.throw(_("{0} must be between {1} and {2}").format(label, minimum, maximum))
    return number


def _choice(value: str | None, *, default: str, allowed: set[str], label: str) -> str:
    selected = str(value or default)
    if selected not in allowed:
        frappe.throw(_("Invalid {0}: {1}").format(label, selected))
    return selected


def _pick_np_contact(contacts: list[dict], recipient_phone: str, recipient_name: str) -> dict | None:
    if not contacts:
        return None
    phone_digits = _normalize_phone(recipient_phone or "")
    name_l = (recipient_name or "").strip().lower()

    def phones(c):
        vals = str(c.get("Phones") or c.get("Phone") or "")
        return _normalize_phone(vals)

    # 1) exact phone match + name hint
    for c in contacts:
        ph = phones(c)
        desc = str(c.get("Description") or c.get("FullName") or "").lower()
        if ph and phone_digits and phone_digits in ph and (not name_l or name_l in desc):
            return c

    # 2) phone match only
    for c in contacts:
        ph = phones(c)
        if ph and phone_digits and phone_digits in ph:
            return c

    # 3) name hint only
    for c in contacts:
        desc = str(c.get("Description") or c.get("FullName") or "").lower()
        if name_l and name_l in desc:
            return c

    return contacts[0]


def _sender_profiles_from_doctype() -> list[dict]:
    if not frappe.db.exists("DocType", "NP Sender Profile"):
        return []
    rows = frappe.get_all(
        "NP Sender Profile",
        fields=[
            "name", "profile_name", "is_active", "is_default", "sender_ref",
            "default_settlement_ref", "default_warehouse_ref", "contact_ref", "phone",
        ],
        filters={"is_active": 1},
        order_by="is_default desc, modified desc",
    )
    out = []
    for r in rows:
        doc = frappe.get_doc("NP Sender Profile", r["name"])
        sender_city_ref = r.get("default_settlement_ref")
        sender_address_ref = r.get("default_warehouse_ref")
        if not sender_city_ref or not sender_address_ref:
            branches = doc.get("sender_branches") or []
            preferred = None
            for b in branches:
                if b.get("is_default"):
                    preferred = b
                    break
            if not preferred and branches:
                preferred = branches[0]
            if preferred:
                sender_city_ref = sender_city_ref or preferred.get("settlement_ref")
                sender_address_ref = sender_address_ref or preferred.get("warehouse_ref")

        out.append({
            "name": r.get("profile_name") or r.get("name"),
            "default": bool(r.get("is_default")),
            "api_key": doc.get_password("api_key"),
            "sender_ref": r.get("sender_ref"),
            "sender_city_ref": sender_city_ref,
            "sender_address_ref": sender_address_ref,
            "contact_sender_ref": r.get("contact_ref"),
            "sender_phone": r.get("phone"),
        })
    return out


def _sender_profiles() -> list[dict]:
    doctype_profiles = _sender_profiles_from_doctype()
    if doctype_profiles:
        return doctype_profiles

    raw = _cfg("novaposhta_senders", [])
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    profiles = []
    for r in raw or []:
        profiles.append(
            {
                "name": r.get("name") or "default",
                "default": bool(r.get("default")),
                "api_key": r.get("api_key") or _cfg("novaposhta_api_key"),
                "sender_ref": r.get("sender_ref") or _cfg("novaposhta_sender_ref"),
                "sender_city_ref": r.get("default_settlement_ref") or r.get("sender_city_ref") or _cfg("novaposhta_sender_city_ref"),
                "sender_address_ref": r.get("default_warehouse_ref") or r.get("sender_address_ref") or _cfg("novaposhta_sender_address_ref"),
                "contact_sender_ref": r.get("contact_sender_ref") or _cfg("novaposhta_contact_sender_ref"),
                "sender_phone": r.get("sender_phone") or _cfg("novaposhta_sender_phone"),
            }
        )
    if not profiles:
        profiles = [
            {
                "name": "default",
                "default": True,
                "api_key": _cfg("novaposhta_api_key"),
                "sender_ref": _cfg("novaposhta_sender_ref"),
                "sender_city_ref": _cfg("novaposhta_sender_city_ref"),
                "sender_address_ref": _cfg("novaposhta_sender_address_ref"),
                "contact_sender_ref": _cfg("novaposhta_contact_sender_ref"),
                "sender_phone": _cfg("novaposhta_sender_phone"),
            }
        ]
    return profiles


def _resolve_profile(sender_profile: str | None = None) -> dict:
    profiles = _sender_profiles()
    if sender_profile:
        for p in profiles:
            if (p.get("name") or "") == sender_profile:
                return p
        frappe.throw(_("Nova Poshta sender profile not found or inactive: {0}").format(sender_profile))
    for p in profiles:
        if p.get("default"):
            return p
    return profiles[0]


def get_client(api_key: str | None = None) -> NovaPoshtaClient:
    if not api_key:
        frappe.throw(_("Nova Poshta API key is not configured for the selected sender profile"))
    return NovaPoshtaClient(api_key)


@frappe.whitelist()
def np_sender_profiles_list() -> dict:
    require_roles(*SALES_ROLES)
    items = [{"name": p.get("name"), "default": 1 if p.get("default") else 0} for p in _sender_profiles()]
    return {"ok": True, "items": items}


@frappe.whitelist()
def np_search_settlements(query: str, sender_profile: str | None = None) -> dict:
    require_roles(*SALES_ROLES)
    if not query:
        return {"ok": True, "items": []}
    query = str(query).strip()[:140]
    profile = _resolve_profile(sender_profile)
    cli = get_client(profile.get("api_key"))
    data = cli.call(
        "Address",
        "searchSettlements",
        {"CityName": query, "Limit": 20},
        api_key=profile.get("api_key") or None,
    )
    items = []
    for blk in data.get("data", []):
        for a in blk.get("Addresses", []):
            items.append(
                {
                    "label": a.get("Present"),
                    "settlement_ref": a.get("Ref"),
                    "city_ref": a.get("DeliveryCity"),
                    "main": a.get("MainDescription"),
                }
            )
    return {"ok": True, "items": items}


@frappe.whitelist()
def np_search_warehouses(settlement_ref: str, query: str | None = None, sender_profile: str | None = None) -> dict:
    require_roles(*SALES_ROLES)
    if not settlement_ref:
        frappe.throw(_("Потрібно settlement_ref"))
    profile = _resolve_profile(sender_profile)
    props = {"SettlementRef": settlement_ref, "Limit": 50}
    if query:
        props["FindByString"] = query
    cli = get_client(profile.get("api_key"))
    data = cli.call("Address", "getWarehouses", props, api_key=profile.get("api_key") or None)
    items = []
    for w in data.get("data", []):
        items.append(
            {
                "label": w.get("Description"),
                "ref": w.get("Ref"),
                "number": w.get("Number"),
                "type": w.get("CategoryOfWarehouse"),
                "short": w.get("ShortAddress"),
            }
        )
    return {"ok": True, "items": items}


@frappe.whitelist()
def np_sender_branches(sender_profile: str | None = None) -> dict:
    require_roles(*SALES_ROLES)
    profile = _resolve_profile(sender_profile)
    name = profile.get("name") or sender_profile
    if not name:
        return {"ok": True, "items": []}

    # find profile doc by profile_name or name
    prof_name = frappe.db.get_value("NP Sender Profile", {"profile_name": name}, "name") or frappe.db.get_value("NP Sender Profile", name, "name")
    if not prof_name:
        return {"ok": True, "items": []}

    rows = frappe.get_all(
        "NP Sender Branch Row",
        fields=["name", "settlement_ref", "warehouse_ref", "warehouse_label", "is_default"],
        filters={"parent": prof_name},
        order_by="is_default desc, idx asc",
    )
    items=[]
    for r in rows:
        items.append({
            "name": r.get("name"),
            "label": r.get("warehouse_label") or r.get("warehouse_ref"),
            "settlement_ref": r.get("settlement_ref"),
            "warehouse_ref": r.get("warehouse_ref"),
            "default": 1 if r.get("is_default") else 0,
        })
    return {"ok": True, "items": items}


@frappe.whitelist()
def track_ttn(ttn: str, sender_profile: str | None = None) -> dict:
    require_roles(*SALES_ROLES)
    ttn = str(ttn or "").strip()
    if not ttn or len(ttn) > 36 or not ttn.isdigit():
        frappe.throw(_("TTN is required"))
    profile = _resolve_profile(sender_profile)
    row = get_client(profile.get("api_key")).track(ttn)
    return {"ok": True, "ttn": ttn, "status": row.get("Status") or row.get("StatusCode") or "", "raw": row}


@frappe.whitelist()
def sync_sales_invoice_ttn_statuses(limit: int = 50) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    docs = frappe.get_all(
        "Sales Invoice",
        filters={"np_ttn_number": ["is", "set"]},
        fields=["name", "np_ttn_number", "np_status", "np_sender_profile", "np_last_sync_at"],
        order_by="np_last_sync_at asc, modified asc",
        limit=max(1, min(int(limit or 50), 500)),
    )
    if not docs:
        return {"ok": True, "checked": 0, "updated": 0}

    updated = 0
    failed = 0
    for d in docs:
        ttn = d.get("np_ttn_number")
        if not ttn:
            continue
        try:
            profile = _resolve_profile(d.get("np_sender_profile") or None)
            client = get_client(profile.get("api_key"))
            row = client.track(ttn)
            status = row.get("Status") or row.get("StatusCode") or ""
            if status and status != (d.get("np_status") or ""):
                frappe.db.set_value("Sales Invoice", d["name"], "np_status", status, update_modified=False)
                updated += 1
            frappe.db.set_value("Sales Invoice", d["name"], "np_last_sync_at", frappe.utils.now_datetime(), update_modified=False)
        except Exception:
            failed += 1
            frappe.log_error(frappe.get_traceback(), f"Nova Poshta sync failed for {d['name']}")

    return {"ok": failed == 0, "checked": len(docs), "updated": updated, "failed": failed}


@frappe.whitelist()
def create_ttn_from_sales_invoice(
    sales_invoice: str,
    recipient_city_ref: str,
    recipient_warehouse_ref: str,
    idempotency_key: str,
    recipient_settlement_ref: str | None = None,
    sender_profile: str | None = None,
    recipient_name: str | None = None,
    recipient_phone: str | None = None,
    weight: float = 1.0,
    seats_amount: int = 1,
    declared_cost: float | None = None,
    sender_branch: str | None = None,
    cargo_type: str | None = None,
    payer_type: str | None = None,
    payment_method: str | None = None,
    service_type: str | None = None,
    width: float | None = None,
    length: float | None = None,
    height: float | None = None,
    cod_amount: float | None = None,
    return_delivery_type: str | None = None,
    order_no: str | None = None,
) -> dict:
    require_roles(*SALES_ROLES)
    if not sales_invoice:
        frappe.throw(_("Sales Invoice is required"))
    if not (idempotency_key or "").strip():
        frappe.throw(_("idempotency_key is required"))
    if not recipient_city_ref or not recipient_warehouse_ref:
        frappe.throw(_("Recipient city/warehouse refs are required"))

    si = permitted_doc("Sales Invoice", sales_invoice, "read")
    if int(si.docstatus or 0) != 1:
        frappe.throw(_("Sales Invoice must be submitted"))
    profile = _resolve_profile(sender_profile)

    sender_ref = profile.get("sender_ref")
    sender_city_ref = profile.get("sender_city_ref")
    sender_address_ref = profile.get("sender_address_ref")
    contact_sender_ref = profile.get("contact_sender_ref")
    sender_phone = _normalize_phone(profile.get("sender_phone") or "")
    profile_api_key = profile.get("api_key")

    profile_doc_name = (
        frappe.db.get_value("NP Sender Profile", {"profile_name": profile.get("name")}, "name")
        or profile.get("name")
    )
    if sender_branch:
        if not frappe.db.exists(
            "NP Sender Branch Row",
            {"name": sender_branch, "parent": profile_doc_name},
        ):
            frappe.throw(_("Sender branch does not belong to the selected Nova Poshta profile"))
        br = frappe.get_doc("NP Sender Branch Row", sender_branch)
        sender_city_ref = br.get("settlement_ref") or sender_city_ref
        sender_address_ref = br.get("warehouse_ref") or sender_address_ref

    if not all([sender_ref, sender_city_ref, sender_address_ref, contact_sender_ref, sender_phone, profile_api_key]):
        frappe.throw(_("Nova Poshta sender profile is incomplete"))

    rec_name = (recipient_name or si.customer_name or si.customer or "").strip()
    rec_phone = _normalize_phone(
        recipient_phone
        or getattr(si, "contact_mobile", None)
        or getattr(si, "contact_phone", None)
        or ""
    )
    if not rec_name:
        frappe.throw(_("Вкажіть ПІБ отримувача"))
    if not rec_phone:
        frappe.throw(_("Вкажіть телефон отримувача"))
    if not rec_phone.startswith("380") or len(rec_phone) != 12:
        frappe.throw(_("Recipient phone must be a valid Ukrainian number"))

    weight_value = _bounded_number(weight, default=1, minimum=0.1, maximum=1000, label="Weight")
    seats_value = int(_bounded_number(seats_amount, default=1, minimum=1, maximum=100, label="Seats"))
    cost = _bounded_number(
        declared_cost if declared_cost is not None else si.grand_total,
        default=1,
        minimum=1,
        maximum=1_000_000_000,
        label="Declared cost",
    )
    width_value = _bounded_number(width, default=15, minimum=1, maximum=1000, label="Width")
    length_value = _bounded_number(length, default=10, minimum=1, maximum=1000, label="Length")
    height_value = _bounded_number(height, default=5, minimum=1, maximum=1000, label="Height")
    cod_value = _bounded_number(cod_amount, default=0, minimum=0, maximum=cost, label="COD amount")
    selected_cargo_type = _choice(
        cargo_type,
        default="Parcel",
        allowed={"Cargo", "Documents", "TiresWheels", "Pallet", "Parcel"},
        label="cargo type",
    )
    selected_payer_type = _choice(
        payer_type,
        default="Recipient",
        allowed={"Sender", "Recipient", "ThirdPerson"},
        label="payer type",
    )
    selected_payment_method = _choice(
        payment_method,
        default="Cash",
        allowed={"Cash", "NonCash"},
        label="payment method",
    )
    selected_service_type = _choice(
        service_type,
        default="WarehouseWarehouse",
        allowed={"WarehouseWarehouse", "WarehouseDoors", "DoorsWarehouse", "DoorsDoors"},
        label="service type",
    )
    selected_return_type = _choice(
        return_delivery_type,
        default="Money",
        allowed={"Money", "Documents"},
        label="return delivery type",
    )

    operation_payload = {
        "sales_invoice": si.name,
        "profile": profile.get("name"),
        "recipient_city_ref": recipient_city_ref,
        "recipient_warehouse_ref": recipient_warehouse_ref,
        "recipient_settlement_ref": recipient_settlement_ref or recipient_city_ref,
        "recipient_name": rec_name,
        "recipient_phone": rec_phone,
        "declared_cost": cost,
        "weight": weight_value,
        "seats_amount": seats_value,
        "width": width_value,
        "length": length_value,
        "height": height_value,
        "cod_amount": cod_value,
        "cargo_type": selected_cargo_type,
        "payer_type": selected_payer_type,
        "payment_method": selected_payment_method,
        "service_type": selected_service_type,
        "return_delivery_type": selected_return_type,
        "sender_city_ref": sender_city_ref,
        "sender_address_ref": sender_address_ref,
        "order_no": order_no or "",
    }
    reservation = reserve_operation(
        idempotency_key=f"nova_poshta:invoice:{si.name}:{idempotency_key}",
        integration="nova_poshta",
        operation_type="create_ttn",
        request_payload=operation_payload,
        reference_doctype="Sales Invoice",
        reference_name=si.name,
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return cached
    if si.get("np_ttn_number"):
        mark_operation(reservation.doc, "failed", error="Sales Invoice already has a Nova Poshta TTN")
        frappe.throw(_("Sales Invoice already has Nova Poshta TTN {0}").format(si.get("np_ttn_number")))

    # Persist the ambiguous state before the first provider-side mutation. A worker
    # crash after this point must never make a blind retry appear safe.
    mark_operation(
        reservation.doc,
        "unknown",
        response_payload={"phase": "external_request_in_progress"},
    )

    cli = get_client(profile_api_key)
    name_parts = rec_name.split()
    first_name = name_parts[0] if name_parts else "Test"
    last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else "Recipient"

    recip = cli.call(
        "Counterparty",
        "save",
        {
            "FirstName": first_name,
            "LastName": last_name,
            "Phone": rec_phone,
            "CounterpartyType": "PrivatePerson",
            "CounterpartyProperty": "Recipient",
        },
        api_key=profile_api_key,
    )
    recip_row = (recip.get("data") or [None])[0]
    if not recip_row:
        frappe.throw(_("НП не повернула отримувача"))

    recip_contacts = cli.call(
        "Counterparty",
        "getCounterpartyContactPersons",
        {"Ref": recip_row.get("Ref")},
        api_key=profile_api_key,
    )
    recip_contact_candidates = recip_contacts.get("data") or []
    recip_contact_row = _pick_np_contact(recip_contact_candidates, rec_phone, rec_name)
    if not recip_contact_row:
        frappe.throw(_("НП не повернула контакт отримувача"))

    payload = {
        "PayerType": selected_payer_type,
        "PaymentMethod": selected_payment_method,
        "DateTime": _today_np(),
        "CargoType": selected_cargo_type,
        "Weight": weight_value,
        "ServiceType": selected_service_type,
        "SeatsAmount": seats_value,
        "Description": f"Замовлення {si.name}",
        "Cost": max(1, int(round(cost))),
        "CitySender": sender_city_ref,
        "Sender": sender_ref,
        "SenderAddress": sender_address_ref,
        "ContactSender": contact_sender_ref,
        "SendersPhone": sender_phone,
        "RecipientType": "PrivatePerson",
        "Recipient": recip_row.get("Ref"),
        "ContactRecipient": recip_contact_row.get("Ref"),
        "RecipientsPhone": rec_phone,
        "RecipientName": rec_name,
        "CityRecipient": recipient_city_ref,
        "RecipientCity": recipient_settlement_ref or recipient_city_ref,
        "RecipientAddress": recipient_warehouse_ref,
        "OptionsSeat": [{
            "volumetricWidth": str(int(round(width_value))),
            "volumetricLength": str(int(round(length_value))),
            "volumetricHeight": str(int(round(height_value))),
            "weight": str(weight_value),
        }],
    }


    if cod_value > 0:
        payload["BackwardDeliveryData"] = [{
            "PayerType": "Recipient",
            "CargoType": selected_return_type,
            "RedeliveryString": str(int(round(cod_value))),
        }]

    if order_no:
        payload["InfoRegClientBarcodes"] = order_no

    try:
        out = cli.call("InternetDocument", "save", payload, api_key=profile_api_key)
        row = (out.get("data") or [{}])[0]
        ttn = row.get("IntDocNumber") or ""
        ttn_ref = row.get("Ref") or ""
        if not ttn or not ttn_ref:
            raise RuntimeError("Nova Poshta did not return TTN number and Ref")
    except Exception:
        mark_operation(reservation.doc, "unknown", error=frappe.get_traceback())
        raise

    if "np_ttn_number" in si.meta.get_valid_columns() and ttn:
        si.db_set("np_ttn_number", ttn, update_modified=False)
    if "np_ttn_ref" in si.meta.get_valid_columns() and ttn_ref:
        si.db_set("np_ttn_ref", ttn_ref, update_modified=False)
    si.db_set("np_sender_profile", frappe.db.get_value("NP Sender Profile", {"profile_name": profile.get("name")}, "name") or profile.get("name"), update_modified=False)

    result = {
        "ok": True,
        "sales_invoice": si.name,
        "ttn_number": ttn,
        "ttn_ref": ttn_ref,
        "label_url": _label_proxy_url(ttn_ref, profile.get("name"), reservation.doc.name),
        "raw": row,
    }
    mark_operation(reservation.doc, "succeeded", external_id=ttn, response_payload=result)
    return result


@frappe.whitelist()
def create_ttn_standalone(
    sender_profile: str | None = None,
    recipient_name: str | None = None,
    recipient_phone: str | None = None,
    recipient_settlement_ref: str | None = None,
    recipient_city_ref: str | None = None,
    recipient_warehouse_ref: str | None = None,
    description: str | None = None,
    declared_cost: float = 100.0,
    weight: float = 1.0,
    seats_amount: int = 1,
    cargo_type: str | None = None,
    payer_type: str | None = None,
    payment_method: str | None = None,
    service_type: str | None = None,
    width: float | None = None,
    length: float | None = None,
    height: float | None = None,
    cod_amount: float | None = None,
    return_delivery_type: str | None = None,
    note: str | None = None,
    order_no: str | None = None,
    sender_branch: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    require_roles(*SALES_ROLES)
    if not idempotency_key:
        frappe.throw(_("idempotency_key is required for standalone TTN creation"))
    if not recipient_name:
        frappe.throw(_("Вкажіть ПІБ отримувача"))
    rec_phone = _normalize_phone(recipient_phone or "")
    if not rec_phone:
        frappe.throw(_("Вкажіть телефон отримувача"))
    if not rec_phone.startswith("380") or len(rec_phone) != 12:
        frappe.throw(_("Recipient phone must be a valid Ukrainian number"))
    if not recipient_city_ref or not recipient_warehouse_ref:
        frappe.throw(_("Вкажіть місто та відділення отримувача"))

    weight_value = _bounded_number(weight, default=1, minimum=0.1, maximum=1000, label="Weight")
    seats_value = int(_bounded_number(seats_amount, default=1, minimum=1, maximum=100, label="Seats"))
    cost = _bounded_number(declared_cost, default=100, minimum=1, maximum=1_000_000_000, label="Declared cost")
    width_value = _bounded_number(width, default=15, minimum=1, maximum=1000, label="Width")
    length_value = _bounded_number(length, default=10, minimum=1, maximum=1000, label="Length")
    height_value = _bounded_number(height, default=5, minimum=1, maximum=1000, label="Height")
    cod_value = _bounded_number(cod_amount, default=0, minimum=0, maximum=cost, label="COD amount")
    selected_cargo_type = _choice(
        cargo_type,
        default="Parcel",
        allowed={"Cargo", "Documents", "TiresWheels", "Pallet", "Parcel"},
        label="cargo type",
    )
    selected_payer_type = _choice(
        payer_type,
        default="Recipient",
        allowed={"Sender", "Recipient", "ThirdPerson"},
        label="payer type",
    )
    selected_payment_method = _choice(
        payment_method,
        default="Cash",
        allowed={"Cash", "NonCash"},
        label="payment method",
    )
    selected_service_type = _choice(
        service_type,
        default="WarehouseWarehouse",
        allowed={"WarehouseWarehouse", "WarehouseDoors", "DoorsWarehouse", "DoorsDoors"},
        label="service type",
    )
    selected_return_type = _choice(
        return_delivery_type,
        default="Money",
        allowed={"Money", "Documents"},
        label="return delivery type",
    )

    profile = _resolve_profile(sender_profile)
    sender_ref = profile.get("sender_ref")
    sender_city_ref = profile.get("sender_city_ref")
    sender_address_ref = profile.get("sender_address_ref")
    contact_sender_ref = profile.get("contact_sender_ref")
    sender_phone = _normalize_phone(profile.get("sender_phone") or "")
    profile_api_key = profile.get("api_key")

    profile_doc_name = (
        frappe.db.get_value("NP Sender Profile", {"profile_name": profile.get("name")}, "name")
        or profile.get("name")
    )
    if sender_branch:
        if not frappe.db.exists(
            "NP Sender Branch Row",
            {"name": sender_branch, "parent": profile_doc_name},
        ):
            frappe.throw(_("Sender branch does not belong to the selected Nova Poshta profile"))
        br = frappe.get_doc("NP Sender Branch Row", sender_branch)
        sender_city_ref = br.get("settlement_ref") or sender_city_ref
        sender_address_ref = br.get("warehouse_ref") or sender_address_ref

    if not all([sender_ref, sender_city_ref, sender_address_ref, contact_sender_ref, sender_phone, profile_api_key]):
        frappe.throw(_("Nova Poshta sender profile is incomplete"))

    reservation = reserve_operation(
        idempotency_key=f"nova_poshta:standalone:{idempotency_key}",
        integration="nova_poshta",
        operation_type="create_ttn_standalone",
        request_payload={
            "profile": profile.get("name"),
            "recipient_name": recipient_name,
            "recipient_phone": rec_phone,
            "recipient_city_ref": recipient_city_ref,
            "recipient_settlement_ref": recipient_settlement_ref or recipient_city_ref,
            "recipient_warehouse_ref": recipient_warehouse_ref,
            "description": (description or note or "Ручна ТТН з ERP")[:90],
            "declared_cost": cost,
            "weight": weight_value,
            "seats_amount": seats_value,
            "width": width_value,
            "length": length_value,
            "height": height_value,
            "cod_amount": cod_value,
            "cargo_type": selected_cargo_type,
            "payer_type": selected_payer_type,
            "payment_method": selected_payment_method,
            "service_type": selected_service_type,
            "return_delivery_type": selected_return_type,
            "sender_city_ref": sender_city_ref,
            "sender_address_ref": sender_address_ref,
            "order_no": order_no or "",
        },
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return cached

    mark_operation(
        reservation.doc,
        "unknown",
        response_payload={"phase": "external_request_in_progress"},
    )

    cli = get_client(profile_api_key)
    name_parts = (recipient_name or "").split()
    first_name = name_parts[0] if name_parts else "Test"
    last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else "Recipient"

    recip = cli.call(
        "Counterparty",
        "save",
        {
            "FirstName": first_name,
            "LastName": last_name,
            "Phone": rec_phone,
            "CounterpartyType": "PrivatePerson",
            "CounterpartyProperty": "Recipient",
        },
        api_key=profile_api_key,
    )
    recip_row = (recip.get("data") or [None])[0]
    if not recip_row:
        frappe.throw(_("НП не повернула отримувача"))

    recip_contacts = cli.call(
        "Counterparty",
        "getCounterpartyContactPersons",
        {"Ref": recip_row.get("Ref")},
        api_key=profile_api_key,
    )
    recip_contact_candidates = recip_contacts.get("data") or []
    recip_contact_row = _pick_np_contact(recip_contact_candidates, rec_phone, (recipient_name or "").strip())
    if not recip_contact_row:
        frappe.throw(_("НП не повернула контакт отримувача"))

    payload = {
        "PayerType": selected_payer_type,
        "PaymentMethod": selected_payment_method,
        "DateTime": _today_np(),
        "CargoType": selected_cargo_type,
        "Weight": weight_value,
        "ServiceType": selected_service_type,
        "SeatsAmount": seats_value,
        "Description": (description or note or "Ручна ТТН з ERP")[:90],
        "Cost": max(1, int(round(cost))),
        "CitySender": sender_city_ref,
        "Sender": sender_ref,
        "SenderAddress": sender_address_ref,
        "ContactSender": contact_sender_ref,
        "SendersPhone": sender_phone,
        "RecipientType": "PrivatePerson",
        "Recipient": recip_row.get("Ref"),
        "ContactRecipient": recip_contact_row.get("Ref"),
        "RecipientsPhone": rec_phone,
        "RecipientName": recipient_name,
        "CityRecipient": recipient_city_ref,
        "RecipientCity": recipient_settlement_ref or recipient_city_ref,
        "RecipientAddress": recipient_warehouse_ref,
        "OptionsSeat": [{
            "volumetricWidth": str(int(round(width_value))),
            "volumetricLength": str(int(round(length_value))),
            "volumetricHeight": str(int(round(height_value))),
            "weight": str(weight_value),
        }],
    }


    if cod_value > 0:
        payload["BackwardDeliveryData"] = [{
            "PayerType": "Recipient",
            "CargoType": selected_return_type,
            "RedeliveryString": str(int(round(cod_value))),
        }]

    if order_no:
        payload["InfoRegClientBarcodes"] = order_no

    try:
        out = cli.call("InternetDocument", "save", payload, api_key=profile_api_key)
        row = (out.get("data") or [{}])[0]
        if not row.get("IntDocNumber") or not row.get("Ref"):
            raise RuntimeError("Nova Poshta did not return TTN number and Ref")
    except Exception:
        mark_operation(reservation.doc, "unknown", error=frappe.get_traceback())
        raise
    result = {
        "ok": True,
        "ttn_number": row.get("IntDocNumber") or "",
        "ttn_ref": row.get("Ref") or "",
        "print_url": _label_proxy_url(row.get("Ref"), profile.get("name"), reservation.doc.name),
        "sticker_url": _label_proxy_url(row.get("Ref"), profile.get("name"), reservation.doc.name),
        "raw": row,
    }
    mark_operation(reservation.doc, "succeeded", external_id=result["ttn_number"], response_payload=result)
    return result


def _label_proxy_url(ttn_ref: str, sender_profile: str | None, operation: str) -> str:
    encoded_ref = quote(str(ttn_ref or ""), safe="")
    encoded_profile = quote(str(sender_profile or ""), safe="")
    encoded_operation = quote(str(operation or ""), safe="")
    return (
        "/api/method/ukrainian_integrations.shipment.nova_poshta.service.download_ttn_label"
        f"?ttn_ref={encoded_ref}&sender_profile={encoded_profile}&operation={encoded_operation}"
    )


@frappe.whitelist()
def download_ttn_label(ttn_ref: str, sender_profile: str | None = None, operation: str | None = None):
    require_roles(*SALES_ROLES)
    ttn_ref = str(ttn_ref or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", ttn_ref):
        frappe.throw(_("TTN Ref is invalid"))

    if operation:
        operation_doc = frappe.get_doc("UA Integration Operation", operation)
        if operation_doc.integration != "nova_poshta" or operation_doc.status not in {"succeeded", "reconciled"}:
            frappe.throw(_("The label operation is not valid"), frappe.PermissionError)
        if load_response(operation_doc).get("ttn_ref") != ttn_ref:
            frappe.throw(_("The TTN does not belong to this operation"), frappe.PermissionError)
        try:
            operation_request = json.loads(operation_doc.request_payload or "{}")
        except (TypeError, ValueError):
            operation_request = {}
        if str(operation_request.get("profile") or "") != str(sender_profile or ""):
            frappe.throw(_("The sender profile does not belong to this operation"), frappe.PermissionError)
        if operation_doc.reference_doctype and operation_doc.reference_name:
            permitted_doc(operation_doc.reference_doctype, operation_doc.reference_name, "read")
        else:
            roles = set(frappe.get_roles())
            if operation_doc.owner != frappe.session.user and not roles.intersection({"Sales Manager", "System Manager"}):
                frappe.throw(_("The TTN does not belong to the current user"), frappe.PermissionError)
    else:
        # Compatibility path for links issued before operation-bound URLs existed.
        invoice = frappe.db.get_value("Sales Invoice", {"np_ttn_ref": ttn_ref}, "name")
        if not invoice:
            frappe.throw(_("A matching Sales Invoice was not found"), frappe.PermissionError)
        invoice_doc = permitted_doc("Sales Invoice", invoice, "read")
        stored_profile = str(invoice_doc.get("np_sender_profile") or "")
        if stored_profile and stored_profile != str(sender_profile or ""):
            frappe.throw(_("The sender profile does not match the Sales Invoice"), frappe.PermissionError)

    profile = _resolve_profile(sender_profile)
    api_key = profile.get("api_key")
    url = f"https://my.novaposhta.ua/orders/printMarking100x100/orders[]/{quote(str(ttn_ref), safe='')}/type/pdf/apiKey/{quote(str(api_key), safe='')}"
    max_size = max(1_000_000, min(int(frappe.conf.get("novaposhta_label_max_bytes", 10_000_000) or 10_000_000), 50_000_000))
    with requests.get(url, timeout=30, stream=True) as response:
        if response.status_code >= 400:
            frappe.throw(_("Nova Poshta label request failed with HTTP {0}").format(response.status_code))
        try:
            content_length = int(response.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            frappe.throw(_("Nova Poshta returned an invalid label size"))
        if content_length > max_size:
            frappe.throw(_("Nova Poshta label exceeds the configured size limit"))
        chunks = []
        downloaded = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            downloaded += len(chunk)
            if downloaded > max_size:
                frappe.throw(_("Nova Poshta label exceeds the configured size limit"))
            chunks.append(chunk)
        content_type = response.headers.get("Content-Type") or ""
    content = b"".join(chunks)
    if "pdf" not in content_type.lower() or not content.startswith(b"%PDF"):
        frappe.throw(_("Nova Poshta returned an invalid label document"))
    frappe.local.response.filename = f"nova-poshta-{ttn_ref}.pdf"
    frappe.local.response.filecontent = content
    frappe.local.response.type = "download"


@frappe.whitelist()
def np_debug_resolve_profile(sender_profile: str | None = None) -> dict:
    require_roles(*SYSTEM_ROLES)
    p = _resolve_profile(sender_profile)
    chk = {
        'sender_ref': p.get('sender_ref'),
        'sender_city_ref': p.get('sender_city_ref'),
        'sender_address_ref': p.get('sender_address_ref'),
        'contact_sender_ref': p.get('contact_sender_ref'),
        'sender_phone': _normalize_phone(p.get('sender_phone') or ''),
        'api_key_configured': bool(p.get('api_key')),
        'name': p.get('name'),
        'default': p.get('default'),
    }
    required = ('sender_ref','sender_city_ref','sender_address_ref','contact_sender_ref','sender_phone','api_key_configured')
    missing = [k for k, v in chk.items() if k in required and not v]
    return {'ok': True, 'profile': chk, 'missing': missing}
