from __future__ import annotations

import json
import re

import frappe
from frappe import _

from ukrainian_integrations.shipment.ukr_poshta.api import UkrPoshtaClient
from ukrainian_integrations.utils.logger import log_event


# ── Config helpers ────────────────────────────────────────────────────────────

def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _normalize_phone(phone: str) -> str:
    """Strip non-digits, ensure +380 prefix."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("380"):
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        return "+38" + digits
    if len(digits) == 9:
        return "+380" + digits
    return "+" + digits if digits else ""




def _up_sender_profiles_list() -> list[dict]:
    if not frappe.db.exists("DocType", "UP Sender Profile"):
        return []
    rows = frappe.get_all(
        "UP Sender Profile",
        fields=[
            "name", "profile_name", "is_active", "is_default", "sender_name", "sender_phone", "sender_email",
            "postcode", "region", "city", "street", "house_number", "apartment_number", "api_base",
        ],
        filters={"is_active": 1},
        order_by="is_default desc, modified desc",
    )
    out=[]
    for r in rows:
        doc = frappe.get_doc("UP Sender Profile", r["name"])
        out.append({
            "name": r.get("profile_name") or r.get("name"),
            "default": bool(r.get("is_default")),
            "sender_name": r.get("sender_name"),
            "sender_phone": r.get("sender_phone"),
            "sender_email": r.get("sender_email"),
            "postcode": r.get("postcode"),
            "region": r.get("region"),
            "city": r.get("city"),
            "street": r.get("street"),
            "house_number": r.get("house_number"),
            "apartment_number": r.get("apartment_number"),
            "api_base": r.get("api_base"),
            "ecom_token": doc.get_password("ecom_token") or _cfg("ukrposhta_ecom_token"),
            "tracking_token": doc.get_password("tracking_token") or _cfg("ukrposhta_tracking_token"),
            "counterparty_token": doc.get_password("counterparty_token") or _cfg("ukrposhta_counterparty_token"),
        })
    return out


def _resolve_up_profile(sender_profile: str | None = None) -> dict:
    profiles = _up_sender_profiles_list()
    if profiles:
        if sender_profile:
            for p in profiles:
                if (p.get("name") or "") == sender_profile:
                    return p
        for p in profiles:
            if p.get("default"):
                return p
        return profiles[0]
    return {
        "name": "default",
        "sender_name": _cfg("ukrposhta_sender_name", "HUNTER"),
        "sender_phone": _cfg("ukrposhta_sender_phone", ""),
        "sender_email": _cfg("ukrposhta_sender_email", ""),
        "postcode": profile.get("postcode") or _cfg("ukrposhta_sender_postcode", ""),
        "region": profile.get("region") or _cfg("ukrposhta_sender_region", ""),
        "city": profile.get("city") or _cfg("ukrposhta_sender_city", ""),
        "street": profile.get("street") or _cfg("ukrposhta_sender_street", ""),
        "house_number": _cfg("ukrposhta_sender_house", ""),
        "apartment_number": _cfg("ukrposhta_sender_apartment", ""),
        "api_base": _cfg("ukrposhta_api_base", "https://www.ukrposhta.ua/ecom/0.0.1"),
        "ecom_token": _cfg("ukrposhta_ecom_token"),
        "tracking_token": _cfg("ukrposhta_tracking_token"),
        "counterparty_token": _cfg("ukrposhta_counterparty_token"),
    }


def _client_from_profile(profile: dict) -> UkrPoshtaClient:
    ecom = profile.get("ecom_token")
    if not ecom:
        frappe.throw(_("Не задано ecom_token для профілю Укрпошти"))
    return UkrPoshtaClient(
        ecom_token=ecom,
        tracking_token=profile.get("tracking_token"),
        counterparty_token=profile.get("counterparty_token"),
        api_base=profile.get("api_base") or _cfg("ukrposhta_api_base", "https://www.ukrposhta.ua/ecom/0.0.1"),
    )


@frappe.whitelist()
def up_sender_profiles_list() -> dict:
    items=[{"name": p.get("name"), "default": 1 if p.get("default") else 0} for p in _up_sender_profiles_list()]
    if not items:
        items=[{"name":"default", "default":1}]
    return {"ok": True, "items": items}


# ── Client factory ────────────────────────────────────────────────────────────

def get_client() -> UkrPoshtaClient:
    ecom = _cfg("ukrposhta_ecom_token")
    tracking = _cfg("ukrposhta_tracking_token")
    counterparty = _cfg("ukrposhta_counterparty_token")
    api_base = _cfg("ukrposhta_api_base", "https://www.ukrposhta.ua/ecom/0.0.1")
    if not ecom:
        frappe.throw(_("Не задано ukrposhta_ecom_token у site_config.json"))
    return UkrPoshtaClient(
        ecom_token=ecom,
        tracking_token=tracking,
        counterparty_token=counterparty,
        api_base=api_base,
    )


# ── Address ───────────────────────────────────────────────────────────────────

@frappe.whitelist()
def up_create_address(address_payload: dict) -> dict:
    """POST /addresses → {ok, address_id, raw}."""
    if isinstance(address_payload, str):
        address_payload = json.loads(address_payload)
    req = {
        "postcode": address_payload.get("postcode"),
        "country": address_payload.get("country") or "UA",
        "region": address_payload.get("region"),
        "city": address_payload.get("city"),
        "district": address_payload.get("district"),
        "street": address_payload.get("street"),
        "houseNumber": address_payload.get("houseNumber"),
        "apartmentNumber": address_payload.get("apartmentNumber") or "",
    }
    missing = [k for k in ("postcode", "region", "city", "street", "houseNumber") if not req.get(k)]
    if missing:
        frappe.throw("Не заповнені поля адреси Укрпошти: " + ", ".join(missing))

    client = get_client()
    try:
        out = client.create_address(req)
        log_event("ukr_poshta", "success", "create_address", request_payload=req, response_payload=out)
    except Exception:
        log_event("ukr_poshta", "error", "create_address failed", request_payload=req, error_trace=frappe.get_traceback())
        raise

    addr_id = out.get("id")
    if not addr_id:
        frappe.throw("Укрпошта не повернула id адреси")
    return {"ok": True, "address_id": str(addr_id), "raw": out}


# ── Client ────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def up_create_client(client_payload: dict) -> dict:
    """POST /clients?token=<counterparty> → {ok, client_uuid, raw}."""
    if isinstance(client_payload, str):
        client_payload = json.loads(client_payload)

    ctype = client_payload.get("type") or "INDIVIDUAL"
    full_name = (client_payload.get("name") or "").strip()
    parts = [x for x in full_name.split() if x]
    first = client_payload.get("firstName") or (parts[0] if parts else "Test")
    last = client_payload.get("lastName") or (" ".join(parts[1:]) if len(parts) > 1 else "Client")

    req: dict = {
        "type": ctype,
        "phoneNumber": _normalize_phone(client_payload.get("phoneNumber") or ""),
        "email": client_payload.get("email") or "",
        "addressId": client_payload.get("addressId"),
        "externalId": client_payload.get("externalId") or "",
    }
    if ctype == "INDIVIDUAL":
        req["firstName"] = first
        req["lastName"] = last
    else:
        req["name"] = full_name or client_payload.get("companyName") or "Company"

    missing = [k for k in ("phoneNumber", "addressId") if not req.get(k)]
    if missing:
        frappe.throw("Не заповнені поля клієнта Укрпошти: " + ", ".join(missing))

    client = get_client()
    try:
        out = client.create_client(req)
        log_event("ukr_poshta", "success", "create_client", request_payload=req, response_payload=out)
    except Exception:
        log_event("ukr_poshta", "error", "create_client failed", request_payload=req, error_trace=frappe.get_traceback())
        raise

    uuid = out.get("uuid") or out.get("id")
    if not uuid:
        frappe.throw("Укрпошта не повернула uuid клієнта")
    return {"ok": True, "client_uuid": str(uuid), "raw": out}


# ── Tracking ──────────────────────────────────────────────────────────────────

@frappe.whitelist()
def track_barcode(barcode: str) -> dict:
    if not barcode:
        frappe.throw(_("Barcode is required"))
    try:
        row = get_client().track(barcode)
        log_event("ukr_poshta", "success", f"Track {barcode}", request_payload={"barcode": barcode}, response_payload=row)
        return {"ok": True, "barcode": barcode, "raw": row}
    except Exception:
        log_event("ukr_poshta", "error", f"Track failed {barcode}", request_payload={"barcode": barcode}, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def sync_sales_invoice_up_statuses(limit: int = 50) -> dict:
    docs = frappe.get_all(
        "Sales Invoice",
        filters={"up_barcode": ["is", "set"]},
        fields=["name", "up_barcode", "up_status"],
        order_by="modified desc",
        limit=max(1, min(int(limit or 50), 500)),
    )
    if not docs:
        return {"ok": True, "checked": 0, "updated": 0}

    client = get_client()
    updated = 0
    for d in docs:
        code = d.get("up_barcode")
        if not code:
            continue
        try:
            row = client.track(code)
            status = row.get("status") or row.get("eventName") or row.get("state") or ""
            if status and status != (d.get("up_status") or ""):
                frappe.db.set_value("Sales Invoice", d["name"], "up_status", status, update_modified=False)
                updated += 1
        except Exception:
            log_event(
                "ukr_poshta",
                "error",
                f"Sync failed for {d['name']}",
                reference_doctype="Sales Invoice",
                reference_name=d["name"],
                request_payload={"barcode": code},
                error_trace=frappe.get_traceback(),
            )

    if updated:
        frappe.db.commit()
    return {"ok": True, "checked": len(docs), "updated": updated}


# ── Full 3-step shipment creation ─────────────────────────────────────────────

@frappe.whitelist()
def create_shipment_from_sales_invoice(
    sales_invoice: str,
    recipient: dict | None = None,
    parcel: dict | None = None,
    sender_profile: str | None = None,
) -> dict:
    """
    Full Ukrposhta eCom 3-step flow:
      1) POST /addresses  (sender + recipient)
      2) POST /clients?token=<counterparty>  (sender + recipient)
      3) POST /shipments?token=<counterparty>
    Saves barcode + shipment_id to Sales Invoice custom fields when present.
    """
    if not sales_invoice:
        frappe.throw(_("Sales Invoice is required"))

    if isinstance(recipient, str):
        recipient = json.loads(recipient)
    if isinstance(parcel, str):
        parcel = json.loads(parcel)

    recipient = recipient or {}
    parcel = parcel or {}

    si = frappe.get_doc("Sales Invoice", sales_invoice)
    profile = _resolve_up_profile(sender_profile)
    client = _client_from_profile(profile)

    # ── 1a. Sender address ────────────────────────────────────────────────────
    sender_addr_payload = {
        "postcode": profile.get("postcode") or _cfg("ukrposhta_sender_postcode", ""),
        "country": "UA",
        "region": profile.get("region") or _cfg("ukrposhta_sender_region", ""),
        "city": profile.get("city") or _cfg("ukrposhta_sender_city", ""),
        "street": profile.get("street") or _cfg("ukrposhta_sender_street", ""),
        "houseNumber": profile.get("house_number") or _cfg("ukrposhta_sender_house", ""),
        "apartmentNumber": profile.get("apartment_number") or _cfg("ukrposhta_sender_apartment", "") or "",
    }
    missing_sender = [k for k in ("postcode", "region", "city", "street", "houseNumber") if not sender_addr_payload.get(k)]
    if missing_sender:
        frappe.throw("Не задані параметри відправника в site_config: " + ", ".join(f"ukrposhta_sender_{k.lower()}" for k in missing_sender))

    sender_addr_out = client.create_address(sender_addr_payload)
    sender_address_id = str(sender_addr_out.get("id") or "")
    if not sender_address_id:
        frappe.throw("Укрпошта не повернула id адреси відправника")

    # ── 1b. Recipient address ─────────────────────────────────────────────────
    recv_postcode = recipient.get("postcode") or ""
    recv_region = recipient.get("region") or ""
    recv_city = recipient.get("city") or ""
    recv_street = recipient.get("street") or ""
    recv_house = recipient.get("house") or recipient.get("houseNumber") or ""

    missing_recv = [k for k, v in {"postcode": recv_postcode, "region": recv_region, "city": recv_city, "street": recv_street, "houseNumber": recv_house}.items() if not v]
    if missing_recv:
        frappe.throw("Не задані поля адреси одержувача: " + ", ".join(missing_recv))

    recv_addr_payload = {
        "postcode": recv_postcode,
        "country": "UA",
        "region": recv_region,
        "city": recv_city,
        "street": recv_street,
        "houseNumber": recv_house,
        "apartmentNumber": recipient.get("apartment") or recipient.get("apartmentNumber") or "",
    }
    recv_addr_out = client.create_address(recv_addr_payload)
    recv_address_id = str(recv_addr_out.get("id") or "")
    if not recv_address_id:
        frappe.throw("Укрпошта не повернула id адреси одержувача")

    # ── 2a. Sender client ─────────────────────────────────────────────────────
    sender_name = (profile.get("sender_name") or _cfg("ukrposhta_sender_name", "") or "").strip()
    sender_phone = _normalize_phone(profile.get("sender_phone") or _cfg("ukrposhta_sender_phone", "") or "")
    sender_email = profile.get("sender_email") or _cfg("ukrposhta_sender_email", "") or ""

    sender_parts = [x for x in sender_name.split() if x]
    sender_req = {
        "type": "INDIVIDUAL",
        "firstName": sender_parts[0] if sender_parts else "Sender",
        "lastName": " ".join(sender_parts[1:]) if len(sender_parts) > 1 else "Company",
        "phoneNumber": sender_phone,
        "email": sender_email,
        "addressId": sender_address_id,
    }
    sender_client_out = client.create_client(sender_req)
    sender_uuid = str(sender_client_out.get("uuid") or sender_client_out.get("id") or "")
    if not sender_uuid:
        frappe.throw("Укрпошта не повернула uuid відправника")

    # ── 2b. Recipient client ──────────────────────────────────────────────────
    recv_name = (recipient.get("name") or si.customer_name or si.customer or "").strip()
    recv_phone = _normalize_phone(
        recipient.get("phone") or recipient.get("phoneNumber") or
        getattr(si, "contact_mobile", None) or getattr(si, "contact_phone", None) or ""
    )
    recv_email = recipient.get("email") or ""

    recv_parts = [x for x in recv_name.split() if x]
    recv_req = {
        "type": "INDIVIDUAL",
        "firstName": recv_parts[0] if recv_parts else "Customer",
        "lastName": " ".join(recv_parts[1:]) if len(recv_parts) > 1 else "Client",
        "phoneNumber": recv_phone,
        "email": recv_email,
        "addressId": recv_address_id,
    }
    recv_client_out = client.create_client(recv_req)
    recv_uuid = str(recv_client_out.get("uuid") or recv_client_out.get("id") or "")
    if not recv_uuid:
        frappe.throw("Укрпошта не повернула uuid одержувача")

    # ── 3. Create shipment ────────────────────────────────────────────────────
    weight = int(round(float(parcel.get("weight") or 1)))
    length = int(round(float(parcel.get("length") or 10)))
    width = int(round(float(parcel.get("width") or 10)))
    height = int(round(float(parcel.get("height") or 5)))
    declared_value = float(parcel.get("declaredPrice") or parcel.get("declared_value") or si.grand_total or 1)
    post_pay = float(parcel.get("postPay") or 0)
    delivery_type = parcel.get("deliveryType") or "W2W"
    on_fail = parcel.get("onFailReceiveType") or "RETURN"
    if on_fail not in {"RETURN", "PROCESS_AS_REFUSAL"}:
        on_fail = "RETURN"

    shipment_req = {
        "sender": {"uuid": sender_uuid},
        "recipient": {"uuid": recv_uuid},
        "deliveryType": delivery_type,
        "weight": weight,
        "length": length,
        "width": width,
        "height": height,
        "postPay": post_pay,
        "recommended": bool(parcel.get("recommended", True)),
        "sms": bool(parcel.get("sms", True)),
        "paidByRecipient": bool(parcel.get("paidByRecipient", False)),
        "description": parcel.get("description") or f"Замовлення {si.name}",
        "onFailReceiveType": on_fail,
        "parcels": parcel.get("parcels") or [
            {
                "name": parcel.get("parcel_name") or "Parcel",
                "weight": weight,
                "length": length,
                "width": width,
                "height": height,
                "declaredPrice": int(round(declared_value)),
            }
        ],
    }

    try:
        out = client.create_shipment(shipment_req)
        log_event("ukr_poshta", "success", f"create_shipment {si.name}", request_payload=shipment_req, response_payload=out)
    except Exception:
        log_event("ukr_poshta", "error", f"create_shipment failed {si.name}", request_payload=shipment_req, error_trace=frappe.get_traceback())
        raise

    barcode = out.get("barcode") or out.get("shipmentBarcode") or out.get("ttn") or out.get("number") or ""
    shipment_id = out.get("uuid") or out.get("id") or out.get("shipmentId") or ""
    status = out.get("status") or out.get("state") or "created"

    valid_cols = si.meta.get_valid_columns()
    if "up_barcode" in valid_cols and barcode:
        si.db_set("up_barcode", barcode, update_modified=False)
    if "up_shipment_id" in valid_cols and shipment_id:
        si.db_set("up_shipment_id", shipment_id, update_modified=False)
    if "up_status" in valid_cols:
        si.db_set("up_status", status, update_modified=False)

    try:
        frappe.get_doc({
            "doctype": "Comment",
            "comment_type": "Info",
            "reference_doctype": "Sales Invoice",
            "reference_name": si.name,
            "content": f"Укрпошта відправлення створено: barcode={barcode or '—'}, id={shipment_id or '—'}",
        }).insert(ignore_permissions=True)
    except Exception:
        pass

    return {
        "ok": True,
        "sales_invoice": si.name,
        "barcode": barcode,
        "shipment_id": shipment_id,
        "status": status,
        "raw": out,
    }
