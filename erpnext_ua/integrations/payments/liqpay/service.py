from __future__ import annotations

import base64
import json
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _

from erpnext_ua.integrations.payments.liqpay.client import (
    LIQPAY_CHECKOUT_URL,
    SUPPORTED_API_VERSIONS,
    LiqPayClient,
)
from erpnext_ua.integrations.utils.logger import log_event
from erpnext_ua.integrations.utils.operations import DOCTYPE as OPERATION_DOCTYPE
from erpnext_ua.integrations.utils.operations import canonical_hash, mark_operation, reserve_operation
from erpnext_ua.integrations.utils.security import (
    ACCOUNTS_MANAGER_ROLES,
    ACCOUNTS_ROLES,
    permitted_doc,
    require_roles,
    secrets_equal,
)
from erpnext_ua.integrations.utils.validation import validate_http_url


def _cfg(key: str, default=None):
    return frappe.conf.get(key, default)


def _liqpay_profiles() -> list[dict]:
    if not frappe.db.exists("DocType", "LiqPay Settings") or not frappe.db.exists("DocType", "LiqPay Profile"):
        return []
    d = frappe.get_single("LiqPay Settings")
    rows = d.get("profiles") or []
    out = []
    for r in rows:
        prv = ""
        if hasattr(r, "get_password"):
            prv = (r.get_password("private_key", raise_exception=False) or "").strip()
        out.append({
                "name": r.get("name"),
                "label": (r.get("label") or "").strip(),
                "enabled": int(r.get("enabled") or 0),
                "is_default": int(r.get("is_default") or 0),
                "public_key": (r.get("public_key") or "").strip(),
                "private_key": prv,
                "result_url": (r.get("result_url") or "").strip(),
                "server_url": (r.get("server_url") or "").strip(),
                "company": (r.get("company") or "").strip(),
                "bank_account": (r.get("bank_account") or "").strip(),
                "mode_of_payment": (r.get("mode_of_payment") or "").strip(),
                "auto_create_payment_entry": int(r.get("auto_create_payment_entry") or 0),
                "allow_sandbox": int(r.get("allow_sandbox") or 0),
        })
    return out


def _pick_profile(profile: str | None = None, public_key: str | None = None) -> dict:
    profs = _liqpay_profiles()
    if not profs:
        public = (_cfg("liqpay_public_key") or "").strip()
        legacy = {
            "name": "site_config",
            "label": "site_config",
            "enabled": 1,
            "is_default": 1,
            "public_key": public,
            "private_key": (_cfg("liqpay_private_key") or "").strip(),
            "result_url": (_cfg("liqpay_result_url") or "").strip(),
            "server_url": (_cfg("liqpay_server_url") or "").strip(),
            "company": (_cfg("liqpay_company") or "").strip(),
            "bank_account": (_cfg("liqpay_bank_account") or "").strip(),
            "mode_of_payment": (_cfg("liqpay_mode_of_payment") or "").strip(),
            "auto_create_payment_entry": int(_cfg("liqpay_auto_create_payment_entry", 0) or 0),
            "allow_sandbox": int(_cfg("liqpay_allow_sandbox", 0) or 0),
        }
        if public_key and public_key != public:
            return {}
        if profile and profile not in {"site_config", "default"}:
            return {}
        return legacy if public and legacy["private_key"] else {}
    if public_key:
        p = next((x for x in profs if x.get("public_key") == public_key and x.get("enabled") == 1), None)
        return p or {}
    if profile:
        p = next((x for x in profs if (x.get("name") == profile or x.get("label") == profile) and x.get("enabled") == 1), None)
        return p or {}
    p = next((x for x in profs if x.get("is_default") == 1 and x.get("enabled") == 1), None)
    if p:
        return p
    p = next((x for x in profs if x.get("enabled") == 1), None)
    return p or {}


def _liqpay_settings() -> dict:
    if not frappe.db.exists("DocType", "LiqPay Settings"):
        return {}
    try:
        d = frappe.get_single("LiqPay Settings")
        return {"enabled": int(d.get("enabled") or 0)}
    except Exception:
        return {}


def _client(public_key: str | None = None, private_key: str | None = None) -> LiqPayClient:
    pub = (public_key or _cfg("liqpay_public_key") or "").strip()
    prv = (private_key or _cfg("liqpay_private_key") or "").strip()
    if not pub or not prv:
        frappe.throw(_("Не задано liqpay_public_key / liqpay_private_key у site_config.json"))
    return LiqPayClient(pub, prv)


def _api_version(value=None) -> int:
    try:
        version = int(value if value is not None else _cfg("liqpay_api_version", 7))
    except (TypeError, ValueError):
        frappe.throw(_("LiqPay API version must be 3 or 7"))
    if version not in SUPPORTED_API_VERSIONS:
        frappe.throw(_("LiqPay API version must be 3 or 7"))
    return version


@frappe.whitelist()
def liqpay_initiate(
    sales_invoice: str,
    idempotency_key: str,
    amount: float | None = None,
    result_url: str | None = None,
    server_url: str | None = None,
    profile: str | None = None,
) -> dict:
    require_roles(*ACCOUNTS_ROLES)
    if not sales_invoice:
        frappe.throw(_("Sales Invoice is required"))
    if not (idempotency_key or "").strip():
        frappe.throw(_("idempotency_key is required"))
    if frappe.db.exists("DocType", "LiqPay Settings") and int(_liqpay_settings().get("enabled") or 0) != 1:
        frappe.throw(_("LiqPay integration is disabled"))
    si = permitted_doc("Sales Invoice", sales_invoice, "read")
    if int(si.docstatus or 0) != 1:
        frappe.throw(_("Sales Invoice must be submitted"))
    if str(si.currency or "").upper() != "UAH":
        frappe.throw(_("LiqPay checkout currently supports only UAH Sales Invoices"))
    outstanding = float(si.outstanding_amount or 0)
    if not Decimal(str(outstanding)).is_finite() or outstanding <= 0:
        frappe.throw(_("Sales Invoice has no positive outstanding amount"))
    try:
        amount_decimal = Decimal(
            str(amount if amount is not None else outstanding)
        ).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw(_("Amount must be a valid monetary value"))
    if not amount_decimal.is_finite() or amount_decimal <= 0:
        frappe.throw(_("Amount must be > 0"))
    amt = float(amount_decimal)
    if amt > outstanding + 0.005:
        frappe.throw(_("Payment amount exceeds invoice outstanding amount"))

    prof = _pick_profile(profile)
    if _liqpay_profiles() and not prof:
        frappe.throw(_("Enabled LiqPay profile not found"))
    if not prof:
        frappe.throw(_("LiqPay credentials are not configured"))
    if prof.get("company") and prof.get("company") != si.company:
        frappe.throw(_("The LiqPay profile company does not match the Sales Invoice company"))
    client = _client(public_key=(prof.get("public_key") or None), private_key=(prof.get("private_key") or None))
    configured_result_url = prof.get("result_url") or _cfg("liqpay_result_url")
    configured_server_url = prof.get("server_url") or _cfg("liqpay_server_url")
    if not configured_server_url:
        frappe.throw(_("LiqPay server callback URL is required"))
    if result_url and result_url != configured_result_url:
        frappe.throw(_("LiqPay Result URL must match the selected server-side profile"))
    if server_url and server_url != configured_server_url:
        frappe.throw(_("LiqPay Server URL must match the selected server-side profile"))
    if result_url or configured_result_url:
        validate_http_url(result_url or configured_result_url, "LiqPay Result URL")
    validate_http_url(server_url or configured_server_url, "LiqPay Server URL")

    request_token = canonical_hash({"idempotency_key": idempotency_key})[:16]
    order_id = f"SI-{si.name}-{request_token}"
    api_version = _api_version()
    payload = {
        "version": api_version,
        "public_key": client.public_key,
        "action": "pay",
        "amount": amt,
        "currency": "UAH",
        "description": f"Оплата рахунку {si.name}",
        "order_id": order_id,
        "result_url": result_url or configured_result_url,
        "server_url": server_url or configured_server_url,
    }
    reservation = reserve_operation(
        idempotency_key=f"liqpay:{order_id}",
        integration="liqpay",
        operation_type="payment",
        request_payload={
            "sales_invoice": si.name,
            "order_id": order_id,
            "amount": round(amt, 2),
            "currency": "UAH",
            "api_version": api_version,
            "public_key": client.public_key,
            "profile": prof.get("name"),
            "result_url": payload.get("result_url"),
            "server_url": payload.get("server_url"),
        },
        reference_doctype="Sales Invoice",
        reference_name=si.name,
    )
    if not reservation.created and reservation.doc.status != "started":
        frappe.throw(
            _("The checkout operation already has status {0}; use a new idempotency key only for a new payment attempt").format(
                reservation.doc.status
            ),
            frappe.ValidationError,
        )
    form = client.cnb_form_payload(payload)
    reservation.doc.response_payload = json.dumps({"checkout_generated": True}, ensure_ascii=False)
    reservation.doc.save(ignore_permissions=True)
    log_event("liqpay", "queued", f"Initiate {si.name} profile:{prof.get('label') or prof.get('name') or 'default'}", reference_doctype="Sales Invoice", reference_name=si.name, request_payload=payload)
    return {
        "ok": True,
        "sales_invoice": si.name,
        "order_id": order_id,
        "checkout_url": LIQPAY_CHECKOUT_URL,
        "api_version": api_version,
        "data": form["data"],
        "signature": form["signature"],
    }


@frappe.whitelist(allow_guest=True)
def liqpay_callback(data: str | None = None, signature: str | None = None):
    if not data or not signature:
        frappe.local.response["http_status_code"] = 400
        return {"ok": False, "error": "missing_data_or_signature"}

    try:
        if len(data) > 100_000 or len(signature) > 512:
            raise ValueError("payload_too_large")
        decoded = json.loads(base64.b64decode(data, validate=True).decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("payload_not_object")
    except Exception:
        frappe.local.response["http_status_code"] = 400
        return {"ok": False, "error": "invalid_data_payload"}

    # A callback public key must match an enabled profile; never fall back to another secret.
    prof = _pick_profile(public_key=(decoded.get("public_key") or None))
    if not prof:
        frappe.local.response["http_status_code"] = 401
        return {"ok": False, "error": "unknown_public_key"}
    client = _client(public_key=(prof.get("public_key") or None), private_key=(prof.get("private_key") or None))

    try:
        callback_version = _api_version(decoded.get("version"))
    except frappe.ValidationError:
        frappe.local.response["http_status_code"] = 400
        return {"ok": False, "error": "unsupported_api_version"}
    expected = client.make_signature(data, callback_version)
    if not secrets_equal(signature, expected):
        frappe.local.response["http_status_code"] = 401
        return {"ok": False, "error": "invalid_signature"}

    order_id = str(decoded.get("order_id") or "").strip()
    status = str(decoded.get("status") or "").strip()
    tx_id = str(
        decoded.get("transaction_id")
        or decoded.get("payment_id")
        or decoded.get("liqpay_order_id")
        or ""
    ).strip()
    if len(order_id) > 180 or len(status) > 64 or len(tx_id) > 140:
        frappe.local.response["http_status_code"] = 400
        return {"ok": False, "error": "callback_fields_too_long"}
    if not order_id or not status:
        frappe.local.response["http_status_code"] = 400
        return {"ok": False, "error": "missing_order_or_status"}

    operation_name = frappe.db.get_value(OPERATION_DOCTYPE, {"idempotency_key": f"liqpay:{order_id}"}, "name")
    if not operation_name:
        frappe.local.response["http_status_code"] = 404
        return {"ok": False, "error": "unknown_order_id"}
    frappe.db.sql(
        "SELECT name FROM `tabUA Integration Operation` WHERE name = %s FOR UPDATE",
        (operation_name,),
    )
    operation = frappe.get_doc(OPERATION_DOCTYPE, operation_name)
    try:
        expected_request = json.loads(operation.request_payload or "{}")
    except Exception:
        expected_request = {}

    try:
        callback_amount = Decimal(str(decoded.get("amount"))).quantize(Decimal("0.01"))
        expected_amount = Decimal(str(expected_request.get("amount"))).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        frappe.local.response["http_status_code"] = 400
        return {"ok": False, "error": "invalid_amount"}
    if callback_amount != expected_amount or str(decoded.get("currency") or "").upper() != str(expected_request.get("currency") or "").upper():
        frappe.local.response["http_status_code"] = 409
        log_event("liqpay", "error", "Callback amount/currency mismatch", reference_doctype=operation.reference_doctype, reference_name=operation.reference_name, request_payload={"order_id": order_id, "status": status, "amount": str(callback_amount), "currency": decoded.get("currency")})
        return {"ok": False, "error": "amount_or_currency_mismatch"}
    if str(decoded.get("public_key") or "") != str(expected_request.get("public_key") or ""):
        frappe.local.response["http_status_code"] = 409
        return {"ok": False, "error": "public_key_mismatch"}
    expected_version = expected_request.get("api_version")
    if expected_version is not None and callback_version != int(expected_version):
        frappe.local.response["http_status_code"] = 409
        return {"ok": False, "error": "api_version_mismatch"}
    if str(decoded.get("action") or "") != "pay":
        frappe.local.response["http_status_code"] = 409
        return {"ok": False, "error": "payment_action_mismatch"}

    ref = operation.reference_name
    if not ref or not frappe.db.exists("Sales Invoice", ref):
        frappe.local.response["http_status_code"] = 404
        return {"ok": False, "error": "sales_invoice_not_found"}

    if status == "sandbox" and int(prof.get("allow_sandbox") or 0) != 1:
        frappe.local.response["http_status_code"] = 409
        return {"ok": False, "error": "sandbox_not_allowed"}

    success_statuses = {"success", "sandbox"}
    pending_statuses = {
        "3ds_verify",
        "cash_wait",
        "cvv_verify",
        "invoice_wait",
        "otp_verify",
        "prepared",
        "processing",
        "receiver_verify",
        "sender_verify",
        "senderapp_verify",
        "wait_accept",
        "wait_card",
        "wait_compensation",
        "wait_qr",
        "wait_receiver",
        "wait_secure",
        "wait_sender",
    }
    failure_statuses = {"error", "failure", "unsubscribed"}
    if status in success_statuses | {"reversed"} and not tx_id:
        frappe.local.response["http_status_code"] = 400
        return {"ok": False, "error": "missing_transaction_id"}

    conflicting_transaction = frappe.db.get_value(
        OPERATION_DOCTYPE,
        {
            "integration": "liqpay",
            "external_id": str(tx_id),
            "status": ["in", ["succeeded", "verified", "reconciled"]],
            "name": ["!=", operation.name],
        },
        "name",
    ) if tx_id else None
    if conflicting_transaction:
        frappe.local.response["http_status_code"] = 409
        return {"ok": False, "error": "transaction_id_already_used"}

    terminal_success = operation.status in {"verified", "reconciled", "succeeded"}
    if terminal_success:
        if status in success_statuses and operation.external_id == str(tx_id):
            return {"ok": True, "idempotent": True}
        if status == "reversed":
            frappe.db.set_value(
                "Sales Invoice",
                ref,
                "liqpay_status",
                "reversed",
                update_modified=False,
            )
            mark_operation(
                operation,
                "unknown",
                response_payload={
                    "ok": True,
                    "status": "reversed",
                    "reversal_transaction_id": tx_id,
                    "original_transaction_id": operation.external_id,
                    "reconciliation_required": True,
                },
                durable=False,
            )
            log_event(
                "liqpay",
                "error",
                "Verified payment was reversed; accounting reconciliation required",
                reference_doctype="Sales Invoice",
                reference_name=ref,
                request_payload={"order_id": order_id, "status": status, "transaction_id": tx_id},
            )
            return {"ok": True, "reconciliation_required": True}
        frappe.local.response["http_status_code"] = 409
        return {"ok": False, "error": "terminal_payment_state_conflict"}

    si = frappe.get_doc("Sales Invoice", ref)
    if prof.get("company") and prof.get("company") != si.company:
        frappe.local.response["http_status_code"] = 409
        return {"ok": False, "error": "profile_company_mismatch"}
    if status in success_statuses:
        si.db_set("liqpay_status", status, update_modified=False)
        si.db_set("liqpay_transaction_id", str(tx_id), update_modified=False)
        si.db_set("liqpay_paid_amount", float(callback_amount), update_modified=False)
        mark_operation(operation, "verified", external_id=str(tx_id), response_payload={"ok": True, "status": status, "transaction_id": tx_id, "amount": float(callback_amount)}, durable=False)
        payment_entry = _maybe_create_payment_entry(si, float(callback_amount), str(tx_id), prof)
        if payment_entry:
            mark_operation(operation, "reconciled", external_id=str(tx_id), response_payload={"ok": True, "status": status, "transaction_id": tx_id, "payment_entry": payment_entry}, durable=False)
    elif status in pending_statuses:
        mark_operation(operation, "started", external_id=str(tx_id or ""), response_payload={"ok": True, "status": status, "transaction_id": tx_id}, durable=False)
    elif status in failure_statuses | {"reversed"}:
        mark_operation(operation, "failed", external_id=str(tx_id or ""), response_payload={"ok": True, "status": status, "transaction_id": tx_id}, durable=False)
        si.db_set("liqpay_status", status or "unknown", update_modified=False)
    else:
        mark_operation(
            operation,
            "unknown",
            external_id=str(tx_id or ""),
            response_payload={"ok": True, "status": status, "transaction_id": tx_id},
            durable=False,
        )
        si.db_set("liqpay_status", status, update_modified=False)

    log_event(
        "liqpay",
        "success" if status in success_statuses else ("queued" if status in pending_statuses else "error"),
        f"Callback status:{status} tx:{tx_id}",
        reference_doctype="Sales Invoice" if ref else None,
        reference_name=ref,
        request_payload={"order_id": order_id, "status": status, "transaction_id": tx_id, "amount": float(callback_amount), "currency": decoded.get("currency")},
    )
    return {"ok": True}


def _maybe_create_payment_entry(si, amount: float, transaction_id: str, profile: dict) -> str | None:
    if int(profile.get("auto_create_payment_entry") or 0) != 1:
        return None
    bank_account = (profile.get("bank_account") or "").strip()
    mode_of_payment = (profile.get("mode_of_payment") or "").strip()
    if not bank_account or not mode_of_payment:
        raise frappe.ValidationError("LiqPay auto reconciliation requires Bank Account and Mode of Payment")

    existing = frappe.db.get_value(
        "Payment Entry",
        {"reference_no": transaction_id, "docstatus": ["<", 2]},
        "name",
    )
    if existing:
        return existing

    outstanding = float(si.outstanding_amount or 0)
    if outstanding <= 0 or amount > outstanding + 0.005:
        log_event(
            "liqpay",
            "queued",
            "Payment verified but requires manual reconciliation",
            reference_doctype="Sales Invoice",
            reference_name=si.name,
            request_payload={"transaction_id": transaction_id, "amount": amount, "outstanding": outstanding},
        )
        return None

    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    pe = get_payment_entry("Sales Invoice", si.name, party_amount=amount, bank_account=bank_account)
    pe.mode_of_payment = mode_of_payment
    pe.reference_no = transaction_id
    pe.reference_date = frappe.utils.nowdate()
    pe.insert(ignore_permissions=True)
    pe.submit()
    return pe.name


@frappe.whitelist()
def liqpay_list_profiles() -> dict:
    require_roles(*ACCOUNTS_MANAGER_ROLES)
    profs = _liqpay_profiles()
    out = []
    for p in profs:
        out.append({
            "name": p.get("name"),
            "label": p.get("label"),
            "enabled": p.get("enabled"),
            "is_default": p.get("is_default"),
            "public_key": p.get("public_key"),
            "company": p.get("company"),
        })
    return {"ok": True, "count": len(out), "profiles": out}
