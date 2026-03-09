from __future__ import annotations

import json
import frappe
from frappe import _

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
            "api_key": doc.get_password("api_key") or _cfg("novaposhta_api_key"),
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
    for p in profiles:
        if p.get("default"):
            return p
    return profiles[0]


def get_client() -> NovaPoshtaClient:
    api_key = _cfg("novaposhta_api_key")
    if not api_key:
        frappe.throw(_("Не задано novaposhta_api_key у site_config.json"))
    return NovaPoshtaClient(api_key)


@frappe.whitelist()
def np_sender_profiles_list() -> dict:
    items = [{"name": p.get("name"), "default": 1 if p.get("default") else 0} for p in _sender_profiles()]
    return {"ok": True, "items": items}


@frappe.whitelist()
def np_search_settlements(query: str, sender_profile: str | None = None) -> dict:
    if not query:
        return {"ok": True, "items": []}
    profile = _resolve_profile(sender_profile)
    cli = get_client()
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
    if not settlement_ref:
        frappe.throw(_("Потрібно settlement_ref"))
    profile = _resolve_profile(sender_profile)
    props = {"SettlementRef": settlement_ref, "Limit": 50}
    if query:
        props["FindByString"] = query
    cli = get_client()
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
def track_ttn(ttn: str) -> dict:
    if not ttn:
        frappe.throw(_("TTN is required"))
    row = get_client().track(ttn)
    return {"ok": True, "ttn": ttn, "status": row.get("Status") or row.get("StatusCode") or "", "raw": row}


@frappe.whitelist()
def sync_sales_invoice_ttn_statuses(limit: int = 50) -> dict:
    docs = frappe.get_all(
        "Sales Invoice",
        filters={"np_ttn_number": ["is", "set"]},
        fields=["name", "np_ttn_number", "np_status"],
        order_by="modified desc",
        limit=max(1, min(int(limit or 50), 500)),
    )
    if not docs:
        return {"ok": True, "checked": 0, "updated": 0}

    client = get_client()
    updated = 0
    for d in docs:
        ttn = d.get("np_ttn_number")
        if not ttn:
            continue
        try:
            row = client.track(ttn)
            status = row.get("Status") or row.get("StatusCode") or ""
            if status and status != (d.get("np_status") or ""):
                frappe.db.set_value("Sales Invoice", d["name"], "np_status", status, update_modified=False)
                updated += 1
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Nova Poshta sync failed for {d['name']}")

    if updated:
        frappe.db.commit()
    return {"ok": True, "checked": len(docs), "updated": updated}


@frappe.whitelist()
def create_ttn_from_sales_invoice(
    sales_invoice: str,
    recipient_city_ref: str,
    recipient_warehouse_ref: str,
    recipient_settlement_ref: str | None = None,
    sender_profile: str | None = None,
    recipient_name: str | None = None,
    recipient_phone: str | None = None,
    weight: float = 1.0,
    seats_amount: int = 1,
    declared_cost: float | None = None,
) -> dict:
    if not sales_invoice:
        frappe.throw(_("Sales Invoice is required"))
    if not recipient_city_ref or not recipient_warehouse_ref:
        frappe.throw(_("Recipient city/warehouse refs are required"))

    si = frappe.get_doc("Sales Invoice", sales_invoice)
    profile = _resolve_profile(sender_profile)

    sender_ref = profile.get("sender_ref")
    sender_city_ref = profile.get("sender_city_ref")
    sender_address_ref = profile.get("sender_address_ref")
    contact_sender_ref = profile.get("contact_sender_ref")
    sender_phone = _normalize_phone(profile.get("sender_phone") or "")
    profile_api_key = profile.get("api_key") or _cfg("novaposhta_api_key")

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

    cli = get_client()
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
    recip_contact_row = (recip_contacts.get("data") or [None])[0]
    if not recip_contact_row:
        frappe.throw(_("НП не повернула контакт отримувача"))

    cost = float(declared_cost) if declared_cost is not None else float(si.grand_total or 0)
    payload = {
        "PayerType": payer_type or "Recipient",
        "PaymentMethod": payment_method or "Cash",
        "DateTime": _today_np(),
        "CargoType": cargo_type or "Parcel",
        "Weight": float(weight or 1.0),
        "ServiceType": service_type or "WarehouseWarehouse",
        "SeatsAmount": int(seats_amount or 1),
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
            "volumetricWidth": str(int(round(float(width or 15)))),
            "volumetricLength": str(int(round(float(length or 10)))),
            "volumetricHeight": str(int(round(float(height or 5)))),
            "weight": str(float(weight or 1.0)),
        }],
    }


    if cod_amount and float(cod_amount) > 0:
        payload["BackwardDeliveryData"] = [{
            "PayerType": "Recipient",
            "CargoType": return_delivery_type or "Money",
            "RedeliveryString": str(int(round(float(cod_amount)))),
        }]

    if order_no:
        payload["InfoRegClientBarcodes"] = order_no

    out = cli.call("InternetDocument", "save", payload, api_key=profile_api_key)
    row = (out.get("data") or [{}])[0]
    ttn = row.get("IntDocNumber") or ""
    ttn_ref = row.get("Ref") or ""

    if "np_ttn_number" in si.meta.get_valid_columns() and ttn:
        si.db_set("np_ttn_number", ttn, update_modified=False)
    if "np_ttn_ref" in si.meta.get_valid_columns() and ttn_ref:
        si.db_set("np_ttn_ref", ttn_ref, update_modified=False)

    return {"ok": True, "sales_invoice": si.name, "ttn_number": ttn, "ttn_ref": ttn_ref, "raw": row}


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
) -> dict:
    if not recipient_name:
        frappe.throw(_("Вкажіть ПІБ отримувача"))
    rec_phone = _normalize_phone(recipient_phone or "")
    if not rec_phone:
        frappe.throw(_("Вкажіть телефон отримувача"))
    if not recipient_city_ref or not recipient_warehouse_ref:
        frappe.throw(_("Вкажіть місто та відділення отримувача"))

    profile = _resolve_profile(sender_profile)
    sender_ref = profile.get("sender_ref")
    sender_city_ref = profile.get("sender_city_ref")
    sender_address_ref = profile.get("sender_address_ref")
    contact_sender_ref = profile.get("contact_sender_ref")
    sender_phone = _normalize_phone(profile.get("sender_phone") or "")
    profile_api_key = profile.get("api_key") or _cfg("novaposhta_api_key")

    if not all([sender_ref, sender_city_ref, sender_address_ref, contact_sender_ref, sender_phone, profile_api_key]):
        frappe.throw(_("Nova Poshta sender profile is incomplete"))

    cli = get_client()
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
    recip_contact_row = (recip_contacts.get("data") or [None])[0]
    if not recip_contact_row:
        frappe.throw(_("НП не повернула контакт отримувача"))

    payload = {
        "PayerType": payer_type or "Recipient",
        "PaymentMethod": payment_method or "Cash",
        "DateTime": _today_np(),
        "CargoType": cargo_type or "Parcel",
        "Weight": float(weight or 1.0),
        "ServiceType": service_type or "WarehouseWarehouse",
        "SeatsAmount": int(seats_amount or 1),
        "Description": (description or note or "Ручна ТТН з ERP")[:90],
        "Cost": max(1, int(round(float(declared_cost or 1)))),
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
            "volumetricWidth": str(int(round(float(width or 15)))),
            "volumetricLength": str(int(round(float(length or 10)))),
            "volumetricHeight": str(int(round(float(height or 5)))),
            "weight": str(float(weight or 1.0)),
        }],
    }


    if cod_amount and float(cod_amount) > 0:
        payload["BackwardDeliveryData"] = [{
            "PayerType": "Recipient",
            "CargoType": return_delivery_type or "Money",
            "RedeliveryString": str(int(round(float(cod_amount)))),
        }]

    if order_no:
        payload["InfoRegClientBarcodes"] = order_no

    out = cli.call("InternetDocument", "save", payload, api_key=profile_api_key)
    row = (out.get("data") or [{}])[0]
    return {
        "ok": True,
        "ttn_number": row.get("IntDocNumber") or "",
        "ttn_ref": row.get("Ref") or "",
        "print_url": row.get("Ref") and f"https://my.novaposhta.ua/orders/printDocument/orders[]/{row.get('Ref')}/type/pdf/apiKey/{profile_api_key}",
        "raw": row,
    }


@frappe.whitelist()
def np_debug_resolve_profile(sender_profile: str | None = None) -> dict:
    p = _resolve_profile(sender_profile)
    chk = {
        'sender_ref': p.get('sender_ref'),
        'sender_city_ref': p.get('sender_city_ref'),
        'sender_address_ref': p.get('sender_address_ref'),
        'contact_sender_ref': p.get('contact_sender_ref'),
        'sender_phone': _normalize_phone(p.get('sender_phone') or ''),
        'api_key': (p.get('api_key') or '')[:8] + '...' if p.get('api_key') else '',
        'name': p.get('name'),
        'default': p.get('default'),
    }
    missing = [k for k,v in chk.items() if k in ('sender_ref','sender_city_ref','sender_address_ref','contact_sender_ref','sender_phone','api_key') and not v]
    return {'ok': True, 'profile': chk, 'missing': missing}
