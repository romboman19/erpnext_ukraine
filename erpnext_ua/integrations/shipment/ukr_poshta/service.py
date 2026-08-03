from __future__ import annotations

import json
import math
import re
from contextlib import suppress

import frappe
from frappe import _

from erpnext_ua.integrations.shipment.profile_selection import select_sender_profile
from erpnext_ua.integrations.shipment.ukr_poshta.api import UkrPoshtaClient
from erpnext_ua.integrations.utils.logger import log_event
from erpnext_ua.integrations.utils.operations import mark_operation, require_new_or_return_success, reserve_operation
from erpnext_ua.integrations.utils.security import SALES_MANAGER_ROLES, SALES_ROLES, permitted_doc, require_roles

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


def _validated_barcode(value: str) -> str:
    barcode = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{6,40}", barcode):
        frappe.throw(_("Ukrposhta barcode must contain 6-40 letters or digits"))
    return barcode


def _bounded_number(value, *, default: float, minimum: float, maximum: float, label: str) -> float:
    try:
        number = float(default if value is None else value)
    except (TypeError, ValueError):
        frappe.throw(_("{0} must be numeric").format(label))
    if not math.isfinite(number) or number < minimum or number > maximum:
        frappe.throw(_("{0} must be between {1} and {2}").format(label, minimum, maximum))
    return number


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if str(value).strip().lower() in {"1", "true", "yes"}:
        return True
    if str(value).strip().lower() in {"0", "false", "no"}:
        return False
    frappe.throw(_("Boolean shipment parameter is invalid"))


def _shipment_parameters(parcel: dict, *, default_declared_value: float) -> dict:
    if parcel.get("parcels"):
        frappe.throw(_("Multi-parcel Ukrposhta shipments are not supported by this endpoint"))
    weight_kg = _bounded_number(parcel.get("weight"), default=1, minimum=0.001, maximum=30, label="Weight (kg)")
    declared_value = _bounded_number(
        parcel.get("declaredPrice", parcel.get("declared_value")),
        default=max(1, default_declared_value),
        minimum=0,
        maximum=1_000_000_000,
        label="Declared value",
    )
    post_pay = _bounded_number(
        parcel.get("postPay"),
        default=0,
        minimum=0,
        maximum=max(0, declared_value),
        label="Postpay",
    )
    delivery_type = str(parcel.get("deliveryType") or "W2W")
    if delivery_type not in {"W2W", "W2D", "D2W", "D2D"}:
        frappe.throw(_("Invalid Ukrposhta delivery type"))
    on_fail = str(parcel.get("onFailReceiveType") or "RETURN")
    if on_fail not in {"RETURN", "PROCESS_AS_REFUSAL"}:
        frappe.throw(_("Invalid Ukrposhta onFailReceiveType"))

    length = int(_bounded_number(parcel.get("length"), default=10, minimum=1, maximum=200, label="Length"))
    width = int(_bounded_number(parcel.get("width"), default=10, minimum=1, maximum=200, label="Width"))
    height = int(_bounded_number(parcel.get("height"), default=5, minimum=1, maximum=200, label="Height"))
    return {
        "weight": max(1, int(round(weight_kg * 1000))),
        "length": length,
        "width": width,
        "height": height,
        "declared_value": int(round(declared_value)),
        "post_pay": post_pay,
        "delivery_type": delivery_type,
        "on_fail": on_fail,
        "recommended": _as_bool(parcel.get("recommended"), True),
        "sms": _as_bool(parcel.get("sms"), True),
        "paid_by_recipient": _as_bool(parcel.get("paidByRecipient"), False),
        "description": str(parcel.get("description") or "")[:250],
        "parcel_name": str(parcel.get("parcel_name") or "Parcel")[:40],
    }




def _up_sender_profiles_list() -> list[dict]:
    if not frappe.db.exists("DocType", "UP Sender Profile"):
        return []
    rows = frappe.get_all(
        "UP Sender Profile",
        fields=[
            "name", "profile_name", "company", "is_active", "is_default", "sender_name", "sender_phone", "sender_email",
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
            "company": r.get("company"),
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
            "ecom_token": doc.get_password("ecom_token"),
            "tracking_token": doc.get_password("tracking_token"),
            "counterparty_token": doc.get_password("counterparty_token"),
        })
    return out


def _resolve_up_profile(sender_profile: str | None = None, *, company: str | None = None) -> dict:
    profiles = _up_sender_profiles_list()
    if profiles:
        try:
            return select_sender_profile(
                profiles,
                carrier="Ukrposhta",
                requested=sender_profile,
                company=company,
            )
        except ValueError as exc:
            frappe.throw(_(str(exc)))
    profile = {
        "name": "default",
        "company": _cfg("default_company"),
        "sender_name": _cfg("ukrposhta_sender_name", "HUNTER"),
        "sender_phone": _cfg("ukrposhta_sender_phone", ""),
        "sender_email": _cfg("ukrposhta_sender_email", ""),
        "postcode": _cfg("ukrposhta_sender_postcode", ""),
        "region": _cfg("ukrposhta_sender_region", ""),
        "city": _cfg("ukrposhta_sender_city", ""),
        "street": _cfg("ukrposhta_sender_street", ""),
        "house_number": _cfg("ukrposhta_sender_house", ""),
        "apartment_number": _cfg("ukrposhta_sender_apartment", ""),
        "api_base": _cfg("ukrposhta_api_base", "https://www.ukrposhta.ua/ecom/0.0.1"),
        "ecom_token": _cfg("ukrposhta_ecom_token"),
        "tracking_token": _cfg("ukrposhta_tracking_token"),
        "counterparty_token": _cfg("ukrposhta_counterparty_token"),
    }
    try:
        return select_sender_profile(
            [profile],
            carrier="Ukrposhta",
            requested=sender_profile,
            company=company,
        )
    except ValueError as exc:
        frappe.throw(_(str(exc)))


def _client_from_profile(profile: dict) -> UkrPoshtaClient:
    ecom = profile.get("ecom_token")
    if not ecom:
        frappe.throw(_("Не задано ecom_token для профілю Укрпошти"))
    return UkrPoshtaClient(
        ecom_token=ecom,
        tracking_token=profile.get("tracking_token"),
        counterparty_token=profile.get("counterparty_token"),
        api_base=profile.get("api_base"),
    )


@frappe.whitelist()
def up_sender_profiles_list(company: str | None = None) -> dict:
    require_roles(*SALES_ROLES)
    items = [
        {
            "name": profile.get("name"),
            "company": profile.get("company"),
            "default": 1 if profile.get("default") else 0,
        }
        for profile in _up_sender_profiles_list()
        if not company or profile.get("company") == company
    ]
    if not items:
        fallback_company = _cfg("default_company")
        if not company or company == fallback_company:
            items = [{"name": "default", "company": fallback_company, "default": 1}]
    return {"ok": True, "items": items}


# ── Address ───────────────────────────────────────────────────────────────────

@frappe.whitelist()
def up_create_address(
    address_payload: dict,
    idempotency_key: str,
    sender_profile: str | None = None,
) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    """POST /addresses → {ok, address_id, raw}."""
    if isinstance(address_payload, str):
        address_payload = json.loads(address_payload)
    if not (idempotency_key or "").strip():
        frappe.throw(_("idempotency_key is required"))
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
    if not re.fullmatch(r"\d{5}", str(req["postcode"])):
        frappe.throw(_("Ukrposhta postcode must contain exactly five digits"))

    profile = _resolve_up_profile(sender_profile)

    reservation = reserve_operation(
        idempotency_key=f"ukrposhta:address:{idempotency_key}",
        integration="ukrposhta",
        operation_type="create_address",
        request_payload={"profile": profile.get("name"), "address": req},
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return cached

    client = _client_from_profile(profile)
    mark_operation(reservation.doc, "unknown", response_payload={"phase": "external_request_in_progress"})
    try:
        out = client.create_address(req)
        log_event("ukr_poshta", "success", "create_address", request_payload=req, response_payload=out)
    except Exception:
        log_event("ukr_poshta", "error", "create_address failed", request_payload=req, error_trace=frappe.get_traceback())
        raise

    addr_id = out.get("id")
    if not addr_id:
        mark_operation(reservation.doc, "unknown", error="Ukrposhta did not return address id")
        frappe.throw("Укрпошта не повернула id адреси")
    result = {"ok": True, "address_id": str(addr_id), "raw": out}
    mark_operation(reservation.doc, "succeeded", external_id=str(addr_id), response_payload=result)
    return result


# ── Client ────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def up_create_client(
    client_payload: dict,
    idempotency_key: str,
    sender_profile: str | None = None,
) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    """POST /clients?token=<counterparty> → {ok, client_uuid, raw}."""
    if isinstance(client_payload, str):
        client_payload = json.loads(client_payload)
    if not (idempotency_key or "").strip():
        frappe.throw(_("idempotency_key is required"))

    ctype = client_payload.get("type") or "INDIVIDUAL"
    if ctype not in {"INDIVIDUAL", "COMPANY"}:
        frappe.throw(_("Ukrposhta client type must be INDIVIDUAL or COMPANY"))
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
    if not re.fullmatch(r"\+380\d{9}", req["phoneNumber"]):
        frappe.throw(_("Ukrposhta client phone must be a valid Ukrainian number"))

    profile = _resolve_up_profile(sender_profile)

    reservation = reserve_operation(
        idempotency_key=f"ukrposhta:client:{idempotency_key}",
        integration="ukrposhta",
        operation_type="create_client",
        request_payload={"profile": profile.get("name"), "client": req},
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return cached

    client = _client_from_profile(profile)
    mark_operation(reservation.doc, "unknown", response_payload={"phase": "external_request_in_progress"})
    try:
        out = client.create_client(req)
        log_event("ukr_poshta", "success", "create_client", request_payload=req, response_payload=out)
    except Exception:
        log_event("ukr_poshta", "error", "create_client failed", request_payload=req, error_trace=frappe.get_traceback())
        raise

    uuid = out.get("uuid") or out.get("id")
    if not uuid:
        mark_operation(reservation.doc, "unknown", error="Ukrposhta did not return client uuid")
        frappe.throw("Укрпошта не повернула uuid клієнта")
    result = {"ok": True, "client_uuid": str(uuid), "raw": out}
    mark_operation(reservation.doc, "succeeded", external_id=str(uuid), response_payload=result)
    return result


# ── Tracking ──────────────────────────────────────────────────────────────────

@frappe.whitelist()
def track_barcode(barcode: str, sender_profile: str | None = None) -> dict:
    require_roles(*SALES_ROLES)
    barcode = _validated_barcode(barcode)
    try:
        profile = _resolve_up_profile(sender_profile)
        row = _client_from_profile(profile).track(barcode)
        log_event("ukr_poshta", "success", f"Track {barcode}", request_payload={"barcode": barcode}, response_payload=row)
        return {"ok": True, "barcode": barcode, "raw": row}
    except Exception:
        log_event("ukr_poshta", "error", f"Track failed {barcode}", request_payload={"barcode": barcode}, error_trace=frappe.get_traceback())
        raise


@frappe.whitelist()
def sync_sales_invoice_up_statuses(limit: int = 50) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    docs = frappe.get_all(
        "Sales Invoice",
        filters={"up_barcode": ["is", "set"]},
        fields=["name", "company", "up_barcode", "up_status", "up_sender_profile", "up_last_sync_at"],
        order_by="up_last_sync_at asc, modified asc",
        limit=max(1, min(int(limit or 50), 500)),
    )
    if not docs:
        return {"ok": True, "checked": 0, "updated": 0}

    updated = 0
    failed = 0
    for d in docs:
        code = d.get("up_barcode")
        if not code:
            continue
        try:
            profile = _resolve_up_profile(
                d.get("up_sender_profile") or None,
                company=d.get("company"),
            )
            client = _client_from_profile(profile)
            row = client.track(_validated_barcode(code))
            status = row.get("status") or row.get("eventName") or row.get("state") or ""
            if status and status != (d.get("up_status") or ""):
                frappe.db.set_value("Sales Invoice", d["name"], "up_status", status, update_modified=False)
                updated += 1
            frappe.db.set_value("Sales Invoice", d["name"], "up_last_sync_at", frappe.utils.now_datetime(), update_modified=False)
        except Exception:
            failed += 1
            log_event(
                "ukr_poshta",
                "error",
                f"Sync failed for {d['name']}",
                reference_doctype="Sales Invoice",
                reference_name=d["name"],
                request_payload={"barcode": code},
                error_trace=frappe.get_traceback(),
            )

    return {"ok": failed == 0, "checked": len(docs), "updated": updated, "failed": failed}


# ── Full 3-step shipment creation ─────────────────────────────────────────────

@frappe.whitelist()
def create_shipment_from_sales_invoice(
    sales_invoice: str,
    idempotency_key: str,
    recipient: dict | None = None,
    parcel: dict | None = None,
    sender_profile: str | None = None,
) -> dict:
    require_roles(*SALES_ROLES)
    """
    Full Ukrposhta eCom 3-step flow:
      1) POST /addresses  (sender + recipient)
      2) POST /clients?token=<counterparty>  (sender + recipient)
      3) POST /shipments?token=<counterparty>
    Saves barcode + shipment_id to Sales Invoice custom fields when present.
    """
    if not sales_invoice:
        frappe.throw(_("Sales Invoice is required"))
    if not (idempotency_key or "").strip():
        frappe.throw(_("idempotency_key is required"))

    if isinstance(recipient, str):
        recipient = json.loads(recipient)
    if isinstance(parcel, str):
        parcel = json.loads(parcel)

    recipient = recipient or {}
    parcel = parcel or {}

    si = permitted_doc("Sales Invoice", sales_invoice, "read")
    if int(si.docstatus or 0) != 1:
        frappe.throw(_("Sales Invoice must be submitted"))
    profile = _resolve_up_profile(sender_profile, company=si.company)
    client = _client_from_profile(profile)

    # ── 1a. Sender address ────────────────────────────────────────────────────
    sender_addr_payload = {
        "postcode": profile.get("postcode"),
        "country": "UA",
        "region": profile.get("region"),
        "city": profile.get("city"),
        "street": profile.get("street"),
        "houseNumber": profile.get("house_number"),
        "apartmentNumber": profile.get("apartment_number") or "",
    }
    missing_sender = [k for k in ("postcode", "region", "city", "street", "houseNumber") if not sender_addr_payload.get(k)]
    if missing_sender:
        frappe.throw("Не задані параметри профілю відправника: " + ", ".join(missing_sender))

    recv_postcode = recipient.get("postcode") or ""
    recv_region = recipient.get("region") or ""
    recv_city = recipient.get("city") or ""
    recv_street = recipient.get("street") or ""
    recv_house = recipient.get("house") or recipient.get("houseNumber") or ""
    missing_recv = [
        key
        for key, value in {
            "postcode": recv_postcode,
            "region": recv_region,
            "city": recv_city,
            "street": recv_street,
            "houseNumber": recv_house,
        }.items()
        if not value
    ]
    if missing_recv:
        frappe.throw("Не задані поля адреси одержувача: " + ", ".join(missing_recv))

    sender_name = (profile.get("sender_name") or "").strip()
    sender_phone = _normalize_phone(profile.get("sender_phone") or "")
    if not sender_name or not sender_phone:
        frappe.throw("У профілі відправника потрібні ім'я та телефон")
    recv_name = (recipient.get("name") or si.customer_name or si.customer or "").strip()
    recv_phone = _normalize_phone(
        recipient.get("phone")
        or recipient.get("phoneNumber")
        or getattr(si, "contact_mobile", None)
        or getattr(si, "contact_phone", None)
        or ""
    )
    if not recv_name or not recv_phone:
        frappe.throw("Вкажіть ПІБ і телефон одержувача")
    if not recv_phone.startswith("+380") or len(recv_phone) != 13:
        frappe.throw(_("Recipient phone must be a valid Ukrainian number"))
    if not sender_phone.startswith("+380") or len(sender_phone) != 13:
        frappe.throw(_("Sender phone must be a valid Ukrainian number"))
    if not re.fullmatch(r"\d{5}", str(sender_addr_payload["postcode"])) or not re.fullmatch(
        r"\d{5}", str(recv_postcode)
    ):
        frappe.throw(_("Ukrposhta postcodes must contain exactly five digits"))

    shipment_values = _shipment_parameters(
        parcel,
        default_declared_value=float(si.grand_total or 1),
    )

    reservation = reserve_operation(
        idempotency_key=f"ukrposhta:invoice:{si.name}:{idempotency_key}",
        integration="ukrposhta",
        operation_type="create_shipment",
        request_payload={"sales_invoice": si.name, "profile": profile.get("name"), "recipient": recipient, "parcel": parcel},
        reference_doctype="Sales Invoice",
        reference_name=si.name,
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return cached
    if si.get("up_barcode"):
        mark_operation(reservation.doc, "failed", error="Sales Invoice already has an Ukrposhta shipment")
        frappe.throw(_("Sales Invoice already has Ukrposhta barcode {0}").format(si.get("up_barcode")))

    mark_operation(reservation.doc, "unknown", response_payload={"phase": "external_request_in_progress"})

    sender_addr_out = client.create_address(sender_addr_payload)
    sender_address_id = str(sender_addr_out.get("id") or "")
    if not sender_address_id:
        frappe.throw("Укрпошта не повернула id адреси відправника")

    # ── 1b. Recipient address ─────────────────────────────────────────────────
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
    sender_email = profile.get("sender_email") or ""

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
    if not sender_uuid:
        frappe.throw("Укрпошта не повернула uuid відправника")

    # ── 2b. Recipient client ──────────────────────────────────────────────────
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
    if not recv_uuid:
        frappe.throw("Укрпошта не повернула uuid одержувача")

    # ── 3. Create shipment ────────────────────────────────────────────────────
    shipment_req = {
        "sender": {"uuid": sender_uuid},
        "recipient": {"uuid": recv_uuid},
        "deliveryType": shipment_values["delivery_type"],
        "weight": shipment_values["weight"],
        "length": shipment_values["length"],
        "width": shipment_values["width"],
        "height": shipment_values["height"],
        "postPay": shipment_values["post_pay"],
        "recommended": shipment_values["recommended"],
        "sms": shipment_values["sms"],
        "paidByRecipient": shipment_values["paid_by_recipient"],
        "description": shipment_values["description"] or f"Замовлення {si.name}",
        "onFailReceiveType": shipment_values["on_fail"],
        "parcels": [
            {
                "name": shipment_values["parcel_name"],
                "weight": shipment_values["weight"],
                "length": shipment_values["length"],
                "width": shipment_values["width"],
                "height": shipment_values["height"],
                "declaredPrice": shipment_values["declared_value"],
            }
        ],
    }

    try:
        out = client.create_shipment(shipment_req)
        log_event("ukr_poshta", "success", f"create_shipment {si.name}", request_payload=shipment_req, response_payload=out)
    except Exception:
        mark_operation(reservation.doc, "unknown", error=frappe.get_traceback())
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
    si.db_set("up_sender_profile", frappe.db.get_value("UP Sender Profile", {"profile_name": profile.get("name")}, "name") or profile.get("name"), update_modified=False)

    with suppress(Exception):
        frappe.get_doc({
            "doctype": "Comment",
            "comment_type": "Info",
            "reference_doctype": "Sales Invoice",
            "reference_name": si.name,
            "content": f"Укрпошта відправлення створено: barcode={barcode or '—'}, id={shipment_id or '—'}",
        }).insert(ignore_permissions=True)

    result = {
        "ok": True,
        "sales_invoice": si.name,
        "barcode": barcode,
        "shipment_id": shipment_id,
        "status": status,
        "raw": out,
    }
    mark_operation(reservation.doc, "succeeded", external_id=barcode or shipment_id, response_payload=result)
    return result


@frappe.whitelist()
def create_shipment_standalone(
    sender_profile: str | None = None,
    recipient: dict | None = None,
    parcel: dict | None = None,
    idempotency_key: str | None = None,
) -> dict:
    require_roles(*SALES_ROLES)
    if not idempotency_key:
        frappe.throw(_("idempotency_key is required for standalone shipment creation"))
    if isinstance(recipient, str):
        recipient = json.loads(recipient)
    if isinstance(parcel, str):
        parcel = json.loads(parcel)

    recipient = recipient or {}
    parcel = parcel or {}

    profile = _resolve_up_profile(sender_profile)
    client = _client_from_profile(profile)

    sender_addr_payload = {
        "postcode": profile.get("postcode"),
        "country": "UA",
        "region": profile.get("region"),
        "city": profile.get("city"),
        "street": profile.get("street"),
        "houseNumber": profile.get("house_number"),
        "apartmentNumber": profile.get("apartment_number") or "",
    }
    missing_sender = [key for key in ("postcode", "region", "city", "street", "houseNumber") if not sender_addr_payload.get(key)]
    if missing_sender:
        frappe.throw("Не задані параметри профілю відправника: " + ", ".join(missing_sender))

    recv_addr_payload = {
        "postcode": recipient.get("postcode") or "",
        "country": "UA",
        "region": recipient.get("region") or "",
        "city": recipient.get("city") or "",
        "street": recipient.get("street") or "",
        "houseNumber": recipient.get("house") or recipient.get("houseNumber") or "",
        "apartmentNumber": recipient.get("apartment") or recipient.get("apartmentNumber") or "",
    }
    missing_recv = [k for k, v in {"postcode": recv_addr_payload["postcode"], "region": recv_addr_payload["region"], "city": recv_addr_payload["city"], "street": recv_addr_payload["street"], "houseNumber": recv_addr_payload["houseNumber"]}.items() if not v]
    if missing_recv:
        frappe.throw("Не задані поля адреси одержувача: " + ", ".join(missing_recv))

    sender_name = (profile.get("sender_name") or "").strip()
    sender_phone = _normalize_phone(profile.get("sender_phone") or "")
    recv_name = (recipient.get("name") or "").strip()
    recv_phone = _normalize_phone(recipient.get("phone") or recipient.get("phoneNumber") or "")
    if not sender_name or not sender_phone:
        frappe.throw("У профілі відправника потрібні ім'я та телефон")
    if not recv_name or not recv_phone:
        frappe.throw("Вкажіть ПІБ і телефон одержувача")
    if not recv_phone.startswith("+380") or len(recv_phone) != 13:
        frappe.throw(_("Recipient phone must be a valid Ukrainian number"))
    if not sender_phone.startswith("+380") or len(sender_phone) != 13:
        frappe.throw(_("Sender phone must be a valid Ukrainian number"))
    if not re.fullmatch(r"\d{5}", str(sender_addr_payload["postcode"])) or not re.fullmatch(
        r"\d{5}", str(recv_addr_payload["postcode"])
    ):
        frappe.throw(_("Ukrposhta postcodes must contain exactly five digits"))

    shipment_values = _shipment_parameters(parcel, default_declared_value=1)

    reservation = reserve_operation(
        idempotency_key=f"ukrposhta:standalone:{idempotency_key}",
        integration="ukrposhta",
        operation_type="create_shipment_standalone",
        request_payload={"profile": profile.get("name"), "recipient": recipient, "parcel": parcel},
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return cached

    mark_operation(reservation.doc, "unknown", response_payload={"phase": "external_request_in_progress"})

    sender_addr_out = client.create_address(sender_addr_payload)
    sender_address_id = str(sender_addr_out.get("id") or "")
    if not sender_address_id:
        frappe.throw("Укрпошта не повернула id адреси відправника")

    recv_addr_out = client.create_address(recv_addr_payload)
    recv_address_id = str(recv_addr_out.get("id") or "")
    if not recv_address_id:
        frappe.throw("Укрпошта не повернула id адреси одержувача")

    sender_email = profile.get("sender_email") or ""
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

    recv_parts = [x for x in recv_name.split() if x]
    recv_req = {
        "type": "INDIVIDUAL",
        "firstName": recv_parts[0] if recv_parts else "Customer",
        "lastName": " ".join(recv_parts[1:]) if len(recv_parts) > 1 else "Client",
        "phoneNumber": recv_phone,
        "email": recipient.get("email") or "",
        "addressId": recv_address_id,
    }
    recv_client_out = client.create_client(recv_req)
    recv_uuid = str(recv_client_out.get("uuid") or recv_client_out.get("id") or "")

    shipment_req = {
        "sender": {"uuid": sender_uuid},
        "recipient": {"uuid": recv_uuid},
        "deliveryType": shipment_values["delivery_type"],
        "weight": shipment_values["weight"],
        "length": shipment_values["length"],
        "width": shipment_values["width"],
        "height": shipment_values["height"],
        "postPay": shipment_values["post_pay"],
        "recommended": shipment_values["recommended"],
        "sms": shipment_values["sms"],
        "paidByRecipient": shipment_values["paid_by_recipient"],
        "description": shipment_values["description"] or "Ручне відправлення з ERP",
        "onFailReceiveType": shipment_values["on_fail"],
        "parcels": [{
            "name": shipment_values["parcel_name"],
            "weight": shipment_values["weight"],
            "length": shipment_values["length"],
            "width": shipment_values["width"],
            "height": shipment_values["height"],
            "declaredPrice": shipment_values["declared_value"],
        }],
    }
    try:
        out = client.create_shipment(shipment_req)
    except Exception:
        mark_operation(reservation.doc, "unknown", error=frappe.get_traceback())
        raise
    result = {
        "ok": True,
        "barcode": out.get("barcode") or out.get("shipmentBarcode") or out.get("ttn") or out.get("number") or "",
        "shipment_id": out.get("uuid") or out.get("id") or out.get("shipmentId") or "",
        "status": out.get("status") or out.get("state") or "created",
        "raw": out,
    }
    mark_operation(reservation.doc, "succeeded", external_id=result["barcode"] or result["shipment_id"], response_payload=result)
    return result
