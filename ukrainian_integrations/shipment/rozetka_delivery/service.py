from __future__ import annotations

import base64
import math
import re
from collections import defaultdict
from contextlib import suppress
from urllib.parse import quote

import frappe
import requests
from frappe import _

from ukrainian_integrations.utils.logger import log_event
from ukrainian_integrations.utils.operations import (
    canonical_hash,
    load_response,
    mark_operation,
    require_new_or_return_success,
    reserve_operation,
)
from ukrainian_integrations.utils.security import (
    SALES_MANAGER_ROLES,
    SALES_ROLES,
    permitted_doc,
    require_roles,
)

from .api import RZ_DELIVERY_API_BASE, RZDeliveryAPIError, RZDeliveryClient

PROFILE_DOCTYPE = "RZ Delivery Sender Profile"
REQUIRED_INVOICE_FIELDS = {
    "rz_track_id",
    "rz_status_code",
    "rz_status",
    "rz_sender_profile",
    "rz_last_sync_at",
    "rz_shipping_cost",
    "rz_estimated_delivery_date",
    "rz_payment_fee",
}
TERMINAL_STATUSES = frozenset(
    {
        "clientCanceled",
        "senderCanceled",
        "cancelled",
        "gaveOutForShowcase",
        "gaveOutPartially",
        "gaveOut",
        "returned",
    }
)
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
)
_TRACK_RE = re.compile(r"[A-Za-z0-9-]{4,64}")
_PROFILE_FIELDS = [
    "name",
    "profile_name",
    "is_default",
    "company",
    "api_base",
    "content_language",
    "carrier_id",
    "sender_first_name",
    "sender_middle_name",
    "sender_last_name",
    "sender_phone",
    "sender_city_id",
    "sender_city_label",
    "sender_department_id",
    "sender_department_label",
]


def _normalize_phone(value: str | None) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("0") and len(digits) == 10:
        digits = "38" + digits
    if len(digits) == 9:
        digits = "380" + digits
    return digits


def _validated_phone(value: str | None, label: str) -> str:
    phone = _normalize_phone(value)
    if len(phone) != 12 or not phone.startswith("380"):
        frappe.throw(_("{0} must be a valid Ukrainian phone number").format(label))
    return phone


def _validated_uuid(value: str | None, label: str, *, required: bool = True) -> str:
    normalized = str(value or "").strip()
    if not normalized and not required:
        return ""
    if not _UUID_RE.fullmatch(normalized):
        frappe.throw(_("{0} must be a valid UUID").format(label))
    return normalized.lower()


def _validated_track_id(value: str | None) -> str:
    track_id = str(value or "").strip()
    if not _TRACK_RE.fullmatch(track_id):
        frappe.throw(_("Invalid Rozetka Delivery track ID"))
    return track_id


def _bounded_number(value, *, default: float, minimum: float, maximum: float, label: str) -> float:
    try:
        number = float(default if value is None or value == "" else value)
    except (TypeError, ValueError):
        frappe.throw(_("{0} must be numeric").format(label))
    if not math.isfinite(number) or number < minimum or number > maximum:
        frappe.throw(_("{0} must be between {1} and {2}").format(label, minimum, maximum))
    return number


def _as_bool(value, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    frappe.throw(_("Invalid boolean value"))


def _as_mapping(value, label: str) -> dict:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = frappe.parse_json(value)
        except Exception:
            frappe.throw(_("{0} must be valid JSON").format(label))
    if not isinstance(value, dict):
        frappe.throw(_("{0} must be an object").format(label))
    return value


def _shipment_parameters(parcel: dict | str | None, *, default_insurance_cost: float) -> dict:
    values = _as_mapping(parcel, "Parcel")
    if values.get("parcels"):
        frappe.throw(_("Multi-parcel Rozetka Delivery shipments are not supported"))

    weight = _bounded_number(values.get("weight"), default=1, minimum=0.01, maximum=1000, label="Weight")
    length = _bounded_number(values.get("length"), default=10, minimum=1, maximum=300, label="Length")
    width = _bounded_number(values.get("width"), default=10, minimum=1, maximum=300, label="Width")
    height = _bounded_number(values.get("height"), default=5, minimum=1, maximum=300, label="Height")
    places = int(_bounded_number(values.get("places"), default=1, minimum=1, maximum=100, label="Places"))
    insurance_cost = _bounded_number(
        values.get("insurance_cost"),
        default=max(1, default_insurance_cost),
        minimum=0.01,
        maximum=1_000_000_000,
        label="Insurance cost",
    )
    cost = _bounded_number(
        values.get("cost"),
        default=0,
        minimum=0,
        maximum=1_000_000_000,
        label="COD amount",
    )
    if cost > insurance_cost:
        frappe.throw(_("COD amount cannot exceed the insured value"))
    delivery_payer = str(values.get("delivery_payer") or "sender").strip()
    if delivery_payer not in {"sender", "receiver"}:
        frappe.throw(_("Delivery payer must be sender or receiver"))

    return {
        "weight": weight,
        "length": length,
        "width": width,
        "height": height,
        "places": places,
        "insurance_cost": insurance_cost,
        "cost": cost,
        "delivery_payer": delivery_payer,
    }


def _resolve_profile(sender_profile: str | None = None, *, include_token: bool = True) -> dict:
    if not frappe.db.exists("DocType", PROFILE_DOCTYPE):
        frappe.throw(_("RZ Delivery Sender Profile is not installed; run bench migrate"))

    filters: dict = {"is_active": 1}
    if sender_profile:
        filters["name"] = sender_profile
    rows = frappe.get_all(
        PROFILE_DOCTYPE,
        filters=filters,
        fields=_PROFILE_FIELDS,
        order_by="is_default desc, modified desc",
        limit=1,
    )
    if not rows and sender_profile:
        rows = frappe.get_all(
            PROFILE_DOCTYPE,
            filters={"is_active": 1, "profile_name": sender_profile},
            fields=_PROFILE_FIELDS,
            limit=1,
        )
    if not rows:
        if sender_profile:
            frappe.throw(
                _("Rozetka Delivery sender profile not found or inactive: {0}").format(
                    sender_profile
                )
            )
        frappe.throw(_("No active Rozetka Delivery sender profile is configured"))

    row = rows[0]
    profile = dict(row)
    profile["docname"] = row.name
    profile["name"] = row.profile_name or row.name
    profile["default"] = bool(row.is_default)
    if include_token:
        doc = frappe.get_doc(PROFILE_DOCTYPE, row.name)
        profile["api_token"] = doc.get_password("api_token")
    return profile


def _client_from_profile(profile: dict) -> RZDeliveryClient:
    if profile.get("company"):
        permitted_doc("Company", profile["company"], "read")
    token = profile.get("api_token")
    if not token:
        frappe.throw(_("The selected Rozetka Delivery profile has no API token"))
    return RZDeliveryClient(
        api_token=token,
        api_base=profile.get("api_base") or RZ_DELIVERY_API_BASE,
        content_language=profile.get("content_language") or "uk",
    )


def _directory_client(sender_profile: str | None = None) -> RZDeliveryClient:
    if sender_profile:
        profile = _resolve_profile(sender_profile, include_token=False)
        return RZDeliveryClient(
            api_base=profile.get("api_base") or RZ_DELIVERY_API_BASE,
            content_language=profile.get("content_language") or "uk",
        )
    return RZDeliveryClient()


def _recipient_data(
    *,
    city_id: str,
    department_id: str,
    first_name: str | None,
    middle_name: str | None,
    last_name: str | None,
    phone: str | None,
) -> dict:
    first = str(first_name or "").strip()[:80]
    middle = str(middle_name or "").strip()[:80]
    last = str(last_name or "").strip()[:80]
    if not first or not last:
        frappe.throw(_("Recipient first name and last name are required"))
    recipient = {
        "city": _validated_uuid(city_id, "Recipient city ID"),
        "first_name": first,
        "last_name": last,
        "phone": [_validated_phone(phone, "Recipient phone")],
        "department": _validated_uuid(department_id, "Recipient department ID"),
    }
    if middle:
        recipient["middle_name"] = middle
    return recipient


def _sender_data(profile: dict) -> dict:
    sender = {
        "city": _validated_uuid(profile.get("sender_city_id"), "Sender city ID"),
        "first_name": str(profile.get("sender_first_name") or "").strip()[:80],
        "last_name": str(profile.get("sender_last_name") or "").strip()[:80],
        "phone": [_validated_phone(profile.get("sender_phone"), "Sender phone")],
        "department": _validated_uuid(profile.get("sender_department_id"), "Sender department ID"),
    }
    if not sender["first_name"] or not sender["last_name"]:
        frappe.throw(_("Rozetka Delivery sender profile requires first and last name"))
    middle = str(profile.get("sender_middle_name") or "").strip()[:80]
    if middle:
        sender["middle_name"] = middle
    return sender


def _provider_payload(
    *,
    profile: dict,
    recipient: dict,
    shipment: dict,
    visible_id: str | None,
    description: str | None,
) -> dict:
    payload = {
        "type": "dept-dept",
        "places": shipment["places"],
        "delivery_payer": shipment["delivery_payer"],
        "cost": shipment["cost"],
        "insurance_cost": shipment["insurance_cost"],
        "params": {
            "weight": shipment["weight"],
            "length": shipment["length"],
            "width": shipment["width"],
            "height": shipment["height"],
        },
        "sender": _sender_data(profile),
        "recipient": recipient,
    }
    normalized_visible_id = str(visible_id or "").strip()[:80]
    normalized_description = str(description or "").strip()[:250]
    if normalized_visible_id:
        payload["visible_id"] = normalized_visible_id
    if normalized_description:
        payload["description"] = normalized_description
    carrier = _validated_uuid(profile.get("carrier_id"), "Carrier ID", required=False)
    if carrier:
        payload["carrier"] = carrier
    return payload


def _explicit_rejection(exc: Exception) -> bool:
    if isinstance(exc, RZDeliveryAPIError):
        return True
    if isinstance(exc, requests.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return bool(status and 400 <= status < 500 and status not in {408, 429})
    return False


def _create_at_provider(profile: dict, payload: dict, operation_doc) -> dict:
    try:
        client = _client_from_profile(profile)
    except Exception:
        mark_operation(operation_doc, "failed", error=frappe.get_traceback())
        raise

    mark_operation(
        operation_doc,
        "unknown",
        response_payload={"phase": "external_request_in_progress"},
    )
    try:
        response = client.create_track(payload)
        data = response.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Rozetka Delivery did not return track data")
        track_id = _validated_track_id(data.get("track_id"))
        data["track_id"] = track_id
        return data
    except Exception as exc:
        status = "failed" if _explicit_rejection(exc) else "unknown"
        mark_operation(operation_doc, status, error=frappe.get_traceback())
        log_event(
            "rozetka_delivery",
            "error",
            "create_track failed",
            request_payload={"request_hash": canonical_hash(payload)},
            error_trace=frappe.get_traceback(),
        )
        raise


def _label_proxy_url(track_id: str, sender_profile: str | None, operation: str) -> str:
    return (
        "/api/method/ukrainian_integrations.shipment.rozetka_delivery.service.download_track_label"
        f"?track_id={quote(track_id, safe='')}"
        f"&sender_profile={quote(str(sender_profile or ''), safe='')}"
        f"&operation={quote(str(operation or ''), safe='')}"
    )


def _safe_operation_payload(*, profile: dict, payload: dict, reference: str | None = None) -> dict:
    return {
        "profile": profile.get("name"),
        "reference": reference or "",
        "provider_request_hash": canonical_hash(payload),
    }


def _assert_invoice_schema(invoice) -> None:
    missing = REQUIRED_INVOICE_FIELDS.difference(invoice.meta.get_valid_columns())
    if missing:
        frappe.throw(
            _("Rozetka Delivery fields are missing; run bench migrate first: {0}").format(
                ", ".join(sorted(missing))
            )
        )


def _assert_invoice_operation_winner(invoice_name: str, operation_doc) -> None:
    candidates = frappe.get_all(
        "UA Integration Operation",
        filters={
            "integration": "rozetka_delivery",
            "operation_type": "create_track",
            "reference_doctype": "Sales Invoice",
            "reference_name": invoice_name,
            "status": ["in", ["started", "unknown", "succeeded", "verified", "reconciled"]],
        },
        fields=["name", "status", "external_id"],
        order_by="creation asc, name asc",
        limit_page_length=100,
    )
    if candidates and candidates[0].name != operation_doc.name:
        winner = candidates[0]
        mark_operation(
            operation_doc,
            "failed",
            error=f"Another track operation already exists: {winner.name} ({winner.status})",
        )
        frappe.throw(
            _("Another Rozetka Delivery operation already exists for this Sales Invoice: {0}").format(
                winner.name
            )
        )


def _persist_invoice_result(invoice, profile: dict, data: dict) -> None:
    invoice.db_set("rz_track_id", data["track_id"], update_modified=False)
    invoice.db_set("rz_status_code", "pending_sync", update_modified=False)
    invoice.db_set("rz_status", _("Awaiting status synchronization"), update_modified=False)
    invoice.db_set("rz_sender_profile", profile.get("docname"), update_modified=False)
    invoice.db_set("rz_shipping_cost", data.get("shipping_cost") or 0, update_modified=False)
    invoice.db_set("rz_payment_fee", data.get("payment_fee") or 0, update_modified=False)
    delivery_date = data.get("delivery_date")
    if delivery_date:
        with suppress(Exception):
            invoice.db_set(
                "rz_estimated_delivery_date",
                frappe.utils.getdate(str(delivery_date)[:10]),
                update_modified=False,
            )


@frappe.whitelist()
def rz_sender_profiles_list(company: str | None = None) -> dict:
    require_roles(*SALES_ROLES)
    rows = frappe.get_all(
        PROFILE_DOCTYPE,
        filters={"is_active": 1},
        fields=["name", "profile_name", "is_default", "company"],
        order_by="is_default desc, modified desc",
    )
    items = [
        {
            "name": row.profile_name or row.name,
            "default": 1 if row.is_default else 0,
            "company": row.company or "",
        }
        for row in rows
        if not company or not row.company or row.company == company
    ]
    return {"ok": True, "items": items}


@frappe.whitelist()
def verify_sender_profile(sender_profile: str | None = None) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    profile = _resolve_profile(sender_profile)
    response = _client_from_profile(profile).verify()
    return {
        "ok": True,
        "profile": profile.get("name"),
        "partner_id": response.get("id"),
        "status": response.get("status"),
        "name": response.get("name"),
    }


@frappe.whitelist()
def rz_search_cities(
    query: str,
    sender_profile: str | None = None,
    carrier: str | None = None,
) -> dict:
    require_roles(*SALES_ROLES)
    normalized_query = str(query or "").strip()[:120]
    if len(normalized_query) < 2:
        return {"ok": True, "items": []}
    profile = _resolve_profile(sender_profile, include_token=False) if sender_profile else None
    carrier_id = _validated_uuid(
        carrier or (profile or {}).get("carrier_id"), "Carrier ID", required=False
    )
    client = _directory_client(sender_profile) if profile else _directory_client()
    response = client.search_cities(
        normalized_query,
        carrier=carrier_id or None,
        limit=50,
    )
    items = []
    for row in response.get("data") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        label = row.get("name") or row.get("id")
        if row.get("region_name"):
            label = f"{label}, {row['region_name']}"
        items.append(
            {
                "id": row.get("id"),
                "label": label,
                "name": row.get("name"),
                "region_name": row.get("region_name"),
            }
        )
    return {"ok": True, "items": items}


@frappe.whitelist()
def rz_search_departments(
    city_id: str,
    query: str | None = None,
    sender_profile: str | None = None,
    carrier: str | None = None,
    for_sender: bool | str | int | None = None,
) -> dict:
    require_roles(*SALES_ROLES)
    normalized_city = _validated_uuid(city_id, "City ID")
    profile = _resolve_profile(sender_profile, include_token=False) if sender_profile else None
    carrier_id = _validated_uuid(
        carrier or (profile or {}).get("carrier_id"), "Carrier ID", required=False
    )
    sender_mode = _as_bool(for_sender)
    client = _directory_client(sender_profile) if profile else _directory_client()
    response = client.search_departments(
        normalized_city,
        query=str(query or "").strip()[:120] or None,
        carrier=carrier_id or None,
        can_receive_tracks=True if sender_mode else None,
        can_give_out_tracks=True if not sender_mode else None,
        limit=50,
    )
    items = []
    for row in response.get("data") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        label = row.get("public_name") or row.get("name") or row.get("id")
        if row.get("carrier_name"):
            label = f"{label} — {row['carrier_name']}"
        items.append(
            {
                "id": row.get("id"),
                "label": label,
                "number": row.get("number"),
                "carrier": row.get("carrier"),
                "carrier_name": row.get("carrier_name"),
            }
        )
    return {"ok": True, "items": items}


@frappe.whitelist(methods=["POST"])
def create_track_from_sales_invoice(
    sales_invoice: str,
    recipient_city_id: str,
    recipient_department_id: str,
    idempotency_key: str,
    sender_profile: str | None = None,
    recipient_first_name: str | None = None,
    recipient_middle_name: str | None = None,
    recipient_last_name: str | None = None,
    recipient_phone: str | None = None,
    description: str | None = None,
    parcel: dict | str | None = None,
) -> dict:
    require_roles(*SALES_ROLES)
    if not sales_invoice:
        frappe.throw(_("Sales Invoice is required"))
    if not str(idempotency_key or "").strip():
        frappe.throw(_("idempotency_key is required"))

    invoice = permitted_doc("Sales Invoice", sales_invoice, "read")
    if int(invoice.docstatus or 0) != 1:
        frappe.throw(_("Sales Invoice must be submitted"))
    if str(invoice.currency or "").upper() != "UAH":
        frappe.throw(_("Rozetka Delivery shipments from Sales Invoice require UAH currency"))
    _assert_invoice_schema(invoice)

    profile = _resolve_profile(sender_profile)
    if profile.get("company") and profile.get("company") != invoice.company:
        frappe.throw(_("Rozetka Delivery sender profile belongs to a different company"))

    name_parts = [part for part in str(invoice.customer_name or invoice.customer or "").split() if part]
    first_name = recipient_first_name or (name_parts[0] if name_parts else "")
    last_name = recipient_last_name or (name_parts[-1] if len(name_parts) > 1 else "")
    middle_name = recipient_middle_name or (" ".join(name_parts[1:-1]) if len(name_parts) > 2 else "")
    phone = (
        recipient_phone
        or getattr(invoice, "contact_mobile", None)
        or getattr(invoice, "contact_phone", None)
    )
    recipient = _recipient_data(
        city_id=recipient_city_id,
        department_id=recipient_department_id,
        first_name=first_name,
        middle_name=middle_name,
        last_name=last_name,
        phone=phone,
    )
    shipment = _shipment_parameters(parcel, default_insurance_cost=float(invoice.grand_total or 1))
    payload = _provider_payload(
        profile=profile,
        recipient=recipient,
        shipment=shipment,
        visible_id=invoice.name,
        description=description or f"Замовлення {invoice.name}",
    )

    reservation = reserve_operation(
        idempotency_key=f"rozetka_delivery:invoice:{invoice.name}:{idempotency_key}",
        integration="rozetka_delivery",
        operation_type="create_track",
        request_payload=_safe_operation_payload(profile=profile, payload=payload, reference=invoice.name),
        reference_doctype="Sales Invoice",
        reference_name=invoice.name,
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return cached
    _assert_invoice_operation_winner(invoice.name, reservation.doc)
    if invoice.get("rz_track_id"):
        mark_operation(reservation.doc, "failed", error="Sales Invoice already has a Rozetka Delivery track")
        frappe.throw(
            _("Sales Invoice already has Rozetka Delivery track {0}").format(invoice.rz_track_id)
        )

    data = _create_at_provider(profile, payload, reservation.doc)
    try:
        _persist_invoice_result(invoice, profile, data)
        result = {
            "ok": True,
            "sales_invoice": invoice.name,
            "track_id": data["track_id"],
            "shipping_cost": data.get("shipping_cost"),
            "delivery_date": data.get("delivery_date"),
            "payment_fee": data.get("payment_fee"),
            "sender_profile": profile.get("docname"),
            "label_url": _label_proxy_url(
                data["track_id"], profile.get("name"), reservation.doc.name
            ),
            "operation": reservation.doc.name,
            "raw": data,
        }
        mark_operation(
            reservation.doc,
            "succeeded",
            external_id=data["track_id"],
            response_payload=result,
        )
    except Exception:
        mark_operation(reservation.doc, "unknown", external_id=data["track_id"], error=frappe.get_traceback())
        raise

    log_event(
        "rozetka_delivery",
        "success",
        "create_track",
        reference_doctype="Sales Invoice",
        reference_name=invoice.name,
        response_payload={"track_id": data["track_id"]},
    )
    return result


@frappe.whitelist(methods=["POST"])
def create_track_standalone(
    recipient_city_id: str,
    recipient_department_id: str,
    recipient_first_name: str,
    recipient_last_name: str,
    recipient_phone: str,
    idempotency_key: str,
    sender_profile: str | None = None,
    recipient_middle_name: str | None = None,
    visible_id: str | None = None,
    description: str | None = None,
    parcel: dict | str | None = None,
) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    if not str(idempotency_key or "").strip():
        frappe.throw(_("idempotency_key is required"))
    profile = _resolve_profile(sender_profile)
    recipient = _recipient_data(
        city_id=recipient_city_id,
        department_id=recipient_department_id,
        first_name=recipient_first_name,
        middle_name=recipient_middle_name,
        last_name=recipient_last_name,
        phone=recipient_phone,
    )
    shipment = _shipment_parameters(parcel, default_insurance_cost=1)
    payload = _provider_payload(
        profile=profile,
        recipient=recipient,
        shipment=shipment,
        visible_id=visible_id,
        description=description,
    )
    reservation = reserve_operation(
        idempotency_key=f"rozetka_delivery:standalone:{idempotency_key}",
        integration="rozetka_delivery",
        operation_type="create_track",
        request_payload=_safe_operation_payload(profile=profile, payload=payload),
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return cached

    data = _create_at_provider(profile, payload, reservation.doc)
    result = {
        "ok": True,
        "track_id": data["track_id"],
        "shipping_cost": data.get("shipping_cost"),
        "delivery_date": data.get("delivery_date"),
        "payment_fee": data.get("payment_fee"),
        "sender_profile": profile.get("docname"),
        "label_url": _label_proxy_url(data["track_id"], profile.get("name"), reservation.doc.name),
        "operation": reservation.doc.name,
        "raw": data,
    }
    mark_operation(
        reservation.doc,
        "succeeded",
        external_id=data["track_id"],
        response_payload=result,
    )
    return result


def _status_rows(response: dict) -> list[dict]:
    data = response.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        if data.get("track_id"):
            return [data]
        for key in ("items", "tracks", "statuses"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _status_values(row: dict) -> tuple[str, str]:
    last_status = row.get("last_status")
    if isinstance(last_status, dict):
        code = str(last_status.get("status") or "").strip()
        label = str(last_status.get("status_name") or code).strip()
    else:
        code = str(last_status or row.get("status") or "").strip()
        label = str(row.get("last_status_name") or row.get("status_name") or code).strip()
    return code[:140], label[:140]


def _detail_status(client: RZDeliveryClient, track_id: str) -> tuple[str, str]:
    response = client.get_track(track_id)
    data = response.get("data")
    return _status_values(data if isinstance(data, dict) else {})


def _update_invoice_status(invoice_name: str, code: str, label: str) -> None:
    frappe.db.set_value("Sales Invoice", invoice_name, "rz_status_code", code, update_modified=False)
    frappe.db.set_value("Sales Invoice", invoice_name, "rz_status", label, update_modified=False)
    frappe.db.set_value(
        "Sales Invoice",
        invoice_name,
        "rz_last_sync_at",
        frappe.utils.now_datetime(),
        update_modified=False,
    )


@frappe.whitelist()
def track(track_id: str, sender_profile: str | None = None) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    normalized = _validated_track_id(track_id)
    response = _client_from_profile(_resolve_profile(sender_profile)).get_track(normalized)
    return {"ok": True, "track_id": normalized, "data": response.get("data") or {}}


@frappe.whitelist(methods=["POST"])
def sync_one_invoice_status(sales_invoice: str) -> dict:
    require_roles(*SALES_ROLES)
    invoice = permitted_doc("Sales Invoice", sales_invoice, "read")
    _assert_invoice_schema(invoice)
    track_id = _validated_track_id(invoice.get("rz_track_id"))
    profile = _resolve_profile(invoice.get("rz_sender_profile") or None)
    client = _client_from_profile(profile)
    response = client.get_statuses([track_id])
    rows = _status_rows(response)
    code, label = _status_values(rows[0]) if rows else _detail_status(client, track_id)
    if not code:
        frappe.throw(_("Rozetka Delivery did not return a track status"))
    _update_invoice_status(invoice.name, code, label or code)
    return {"ok": True, "track_id": track_id, "status_code": code, "status": label or code}


@frappe.whitelist(methods=["POST"])
def sync_sales_invoice_rz_statuses(limit: int = 50) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    bounded_limit = max(1, min(int(limit or 50), 500))
    invoices = frappe.get_all(
        "Sales Invoice",
        filters=[
            ["rz_track_id", "is", "set"],
            ["rz_status_code", "not in", sorted(TERMINAL_STATUSES)],
        ],
        fields=[
            "name",
            "rz_track_id",
            "rz_status_code",
            "rz_status",
            "rz_sender_profile",
            "rz_last_sync_at",
        ],
        order_by="rz_last_sync_at asc, modified asc",
        limit=bounded_limit,
    )
    if not invoices:
        return {"ok": True, "checked": 0, "updated": 0, "failed": 0}

    grouped: dict[str, list[dict]] = defaultdict(list)
    for invoice in invoices:
        grouped[str(invoice.get("rz_sender_profile") or "")].append(invoice)

    updated = 0
    failed = 0
    for profile_name, rows in grouped.items():
        try:
            client = _client_from_profile(_resolve_profile(profile_name or None))
        except Exception:
            failed += len(rows)
            frappe.log_error(frappe.get_traceback(), f"Rozetka Delivery profile resolution failed: {profile_name}")
            continue

        for offset in range(0, len(rows), 50):
            batch = rows[offset : offset + 50]
            track_ids = [_validated_track_id(row.get("rz_track_id")) for row in batch]
            try:
                response = client.get_statuses(track_ids)
                by_id = {
                    str(status_row.get("track_id")): status_row
                    for status_row in _status_rows(response)
                    if status_row.get("track_id")
                }
            except Exception:
                failed += len(batch)
                frappe.log_error(
                    frappe.get_traceback(),
                    f"Rozetka Delivery batch status sync failed: {profile_name}",
                )
                continue

            for invoice in batch:
                track_id = str(invoice.get("rz_track_id"))
                try:
                    status_row = by_id.get(track_id)
                    code, label = (
                        _status_values(status_row)
                        if status_row
                        else _detail_status(client, track_id)
                    )
                    if not code:
                        raise RuntimeError("Rozetka Delivery returned an empty status")
                    _update_invoice_status(invoice.name, code, label or code)
                    if code != (invoice.get("rz_status_code") or "") or label != (
                        invoice.get("rz_status") or ""
                    ):
                        updated += 1
                except Exception:
                    failed += 1
                    frappe.log_error(
                        frappe.get_traceback(),
                        f"Rozetka Delivery sync failed for {invoice.name}",
                    )

    return {"ok": failed == 0, "checked": len(invoices), "updated": updated, "failed": failed}


def _authorized_label_context(track_id: str, operation: str | None) -> tuple[str | None, str | None]:
    if operation:
        operation_doc = frappe.get_doc("UA Integration Operation", operation)
        if operation_doc.integration != "rozetka_delivery":
            frappe.throw(_("Operation does not belong to Rozetka Delivery"), frappe.PermissionError)
        operation_response = load_response(operation_doc)
        if (operation_response.get("track_id") or operation_doc.external_id) != track_id:
            frappe.throw(_("Track does not match the operation"), frappe.PermissionError)
        if operation_doc.reference_doctype and operation_doc.reference_name:
            permitted_doc(operation_doc.reference_doctype, operation_doc.reference_name, "read")
        else:
            user = getattr(getattr(frappe, "session", None), "user", None)
            if operation_doc.owner != user:
                require_roles(*SALES_MANAGER_ROLES)
        profile_name = operation_response.get("sender_profile")
        if not profile_name:
            with suppress(Exception):
                request_payload = frappe.parse_json(operation_doc.request_payload or "{}")
                if isinstance(request_payload, dict):
                    profile_name = request_payload.get("profile")
        if operation_doc.reference_doctype == "Sales Invoice":
            profile_name = frappe.db.get_value(
                "Sales Invoice", operation_doc.reference_name, "rz_sender_profile"
            )
        return profile_name, operation_doc.name

    invoice_name = frappe.db.get_value("Sales Invoice", {"rz_track_id": track_id}, "name")
    if not invoice_name:
        frappe.throw(_("Track is not linked to an authorized invoice"), frappe.PermissionError)
    invoice = permitted_doc("Sales Invoice", invoice_name, "read")
    return invoice.get("rz_sender_profile"), None


@frappe.whitelist()
def download_track_label(
    track_id: str,
    sender_profile: str | None = None,
    operation: str | None = None,
):
    require_roles(*SALES_ROLES)
    normalized = _validated_track_id(track_id)
    linked_profile, _ = _authorized_label_context(normalized, operation)
    if not linked_profile:
        frappe.throw(_("Authorized Rozetka Delivery sender profile cannot be resolved"))
    profile = _resolve_profile(linked_profile)
    response = _client_from_profile(profile).get_label([normalized])
    data = response.get("data")
    encoded = data.get("label") if isinstance(data, dict) else None
    if not isinstance(encoded, str) or not encoded.strip():
        frappe.throw(_("Rozetka Delivery did not return a label"))
    encoded = encoded.strip()
    if encoded.lower().startswith("data:application/pdf;base64,"):
        encoded = encoded.split(",", 1)[1]
    encoded = re.sub(r"\s+", "", encoded)
    if len(encoded) > 22 * 1024 * 1024:
        frappe.throw(_("Rozetka Delivery label is too large"))
    try:
        content = base64.b64decode(encoded, validate=True)
    except Exception:
        frappe.throw(_("Rozetka Delivery returned an invalid base64 label"))
    if len(content) > 16 * 1024 * 1024:
        frappe.throw(_("Rozetka Delivery label is too large"))
    if not content.startswith(b"%PDF-"):
        frappe.throw(_("Rozetka Delivery label is not a PDF"))

    frappe.local.response.filename = f"rozetka-delivery-{normalized}.pdf"
    frappe.local.response.filecontent = content
    frappe.local.response.type = "download"
