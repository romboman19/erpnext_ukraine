from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import frappe
from frappe import _

from ukrainian_integrations.utils.logger import sanitize_payload, sanitize_text
from ukrainian_integrations.utils.security import ACCOUNTS_MANAGER_ROLES, SALES_MANAGER_ROLES, require_roles

DOCTYPE = "UA Integration Operation"
FINANCIAL_INTEGRATIONS = frozenset({"ecommerce_payment", "liqpay", "monobank", "privatbank"})
SALES_INTEGRATIONS = frozenset(
    {
        "ecommerce",
        "nova_poshta",
        "prom_ua",
        "rozetka_delivery",
        "telegram",
        "turbosms",
        "ukrposhta",
        "vitalpbx",
    }
)


@dataclass(frozen=True)
class OperationReservation:
    doc: Any
    created: bool


def canonical_hash(payload: Any) -> str:
    raw = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _dump(payload: Any) -> str:
    return sanitize_text(json.dumps(sanitize_payload(payload), ensure_ascii=False, sort_keys=True, default=str))


def load_response(doc) -> dict:
    try:
        value = json.loads(doc.get("response_payload") or "{}")
        return value if isinstance(value, dict) else {"value": value}
    except (TypeError, ValueError):
        return {}


def _assert_same_request(doc, request_payload: Any) -> None:
    """An idempotency key identifies exactly one immutable logical request."""
    expected = canonical_hash(request_payload)
    if doc.request_hash and doc.request_hash != expected:
        frappe.throw(
            _("Idempotency key was already used for a different request"),
            frappe.ValidationError,
        )


def _allowed_operation_integrations() -> set[str] | None:
    user = getattr(getattr(frappe, "session", None), "user", None)
    roles = set(frappe.get_roles(user))
    if user == "Administrator" or "System Manager" in roles:
        return None

    allowed: set[str] = set()
    if "Accounts Manager" in roles:
        allowed.update(FINANCIAL_INTEGRATIONS)
    if "Sales Manager" in roles:
        allowed.update(SALES_INTEGRATIONS)
    return allowed


def _require_operation_access(doc) -> None:
    allowed = _allowed_operation_integrations()
    if allowed is not None and doc.integration not in allowed:
        frappe.throw(_("You do not have permission to access this operation"), frappe.PermissionError)

    if doc.reference_doctype and doc.reference_name:
        permitted_doc = frappe.get_doc(doc.reference_doctype, doc.reference_name)
        permitted_doc.check_permission("read")


def get_permission_query_conditions(user: str | None = None) -> str | None:
    """Keep operation lists partitioned between accounting and sales domains."""
    user = user or getattr(getattr(frappe, "session", None), "user", None)
    roles = set(frappe.get_roles(user))
    if user == "Administrator" or "System Manager" in roles:
        return None

    allowed: set[str] = set()
    if "Accounts Manager" in roles:
        allowed.update(FINANCIAL_INTEGRATIONS)
    if "Sales Manager" in roles:
        allowed.update(SALES_INTEGRATIONS)
    if not allowed:
        return "1=0"
    quoted = ", ".join(frappe.db.escape(value) for value in sorted(allowed))
    return f"`tab{DOCTYPE}`.`integration` in ({quoted})"


def has_permission(doc, user: str | None = None, permission_type: str | None = None) -> bool:
    del permission_type
    user = user or getattr(getattr(frappe, "session", None), "user", None)
    roles = set(frappe.get_roles(user))
    if user == "Administrator" or "System Manager" in roles:
        return True
    if "Accounts Manager" in roles and doc.integration in FINANCIAL_INTEGRATIONS:
        return True
    return "Sales Manager" in roles and doc.integration in SALES_INTEGRATIONS


def reserve_operation(
    *,
    idempotency_key: str,
    integration: str,
    operation_type: str,
    request_payload: Any,
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    retry_failed: bool = False,
    durable: bool = True,
) -> OperationReservation:
    key = (idempotency_key or "").strip()
    if not key:
        frappe.throw(_("Idempotency key is required"))
    if len(key) > 240:
        frappe.throw(_("Idempotency key is too long"))

    existing_name = frappe.db.get_value(DOCTYPE, {"idempotency_key": key}, "name")
    if existing_name:
        doc = frappe.get_doc(DOCTYPE, existing_name)
        _assert_same_request(doc, request_payload)
        if retry_failed and doc.status == "failed":
            doc.status = "started"
            doc.attempts = int(doc.attempts or 0) + 1
            doc.last_error = ""
            doc.completed_at = None
            doc.request_hash = canonical_hash(request_payload)
            doc.request_payload = _dump(request_payload)
            doc.save(ignore_permissions=True)
            if durable:
                frappe.db.commit()
            return OperationReservation(doc=doc, created=True)
        return OperationReservation(doc=doc, created=False)

    values = {
        "doctype": DOCTYPE,
        "idempotency_key": key,
        "integration": integration,
        "operation_type": operation_type,
        "status": "started",
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "request_hash": canonical_hash(request_payload),
        "request_payload": _dump(request_payload),
        "attempts": 1,
    }
    try:
        doc = frappe.get_doc(values)
        doc.insert(ignore_permissions=True)
        if durable:
            frappe.db.commit()
        return OperationReservation(doc=doc, created=True)
    except frappe.DuplicateEntryError:
        # A unique key closes the check/insert race. If another worker won, reuse it.
        existing_name = frappe.db.get_value(DOCTYPE, {"idempotency_key": key}, "name")
        if existing_name:
            doc = frappe.get_doc(DOCTYPE, existing_name)
            _assert_same_request(doc, request_payload)
            return OperationReservation(doc=doc, created=False)
        raise


def mark_operation(
    doc,
    status: str,
    *,
    external_id: str | None = None,
    response_payload: Any = None,
    error: str | None = None,
    durable: bool = True,
) -> None:
    if status not in {"started", "succeeded", "failed", "unknown", "verified", "reconciled"}:
        raise ValueError(f"Unsupported operation status: {status}")
    doc.status = status
    if external_id:
        doc.external_id = str(external_id)
    if response_payload is not None:
        doc.response_payload = _dump(response_payload)
    doc.last_error = sanitize_text(error)[:4000]
    if status in {"succeeded", "failed", "verified", "reconciled"}:
        doc.completed_at = frappe.utils.now_datetime()
    else:
        doc.completed_at = None
    doc.save(ignore_permissions=True)
    if durable:
        frappe.db.commit()


def require_new_or_return_success(reservation: OperationReservation) -> dict | None:
    if reservation.created:
        return None
    status = reservation.doc.status
    if status in {"succeeded", "verified", "reconciled"}:
        return load_response(reservation.doc)
    frappe.throw(
        _("Operation already exists with status {0}; reconcile it before retrying").format(status),
        frappe.ValidationError,
    )


@frappe.whitelist()
def get_operation_status(idempotency_key: str) -> dict:
    require_roles(*(ACCOUNTS_MANAGER_ROLES + SALES_MANAGER_ROLES))
    name = frappe.db.get_value(DOCTYPE, {"idempotency_key": idempotency_key}, "name")
    if not name:
        frappe.throw(_("Operation not found"))
    doc = frappe.get_doc(DOCTYPE, name)
    _require_operation_access(doc)
    return {
        "ok": True,
        "name": doc.name,
        "idempotency_key": doc.idempotency_key,
        "integration": doc.integration,
        "operation_type": doc.operation_type,
        "status": doc.status,
        "external_id": doc.external_id,
        "reference_doctype": doc.reference_doctype,
        "reference_name": doc.reference_name,
        "attempts": doc.attempts,
        "last_error": doc.last_error,
        "response": load_response(doc),
    }


@frappe.whitelist()
def resolve_operation(operation: str, resolution: str, external_id: str | None = None, note: str | None = None) -> dict:
    require_roles(*(ACCOUNTS_MANAGER_ROLES + SALES_MANAGER_ROLES))
    if resolution not in {"failed", "succeeded", "reconciled"}:
        frappe.throw(_("Resolution must be failed, succeeded or reconciled"))
    doc = frappe.get_doc(DOCTYPE, operation)
    _require_operation_access(doc)
    if doc.status not in {"started", "unknown", "verified", "failed"}:
        frappe.throw(_("Operation status cannot be manually resolved: {0}").format(doc.status))
    if resolution in {"succeeded", "reconciled"} and not (external_id or doc.external_id):
        frappe.throw(_("External ID is required for successful resolution"))
    mark_operation(
        doc,
        resolution,
        external_id=external_id or doc.external_id,
        response_payload={
            "manual_resolution": True,
            "note": (note or "")[:1000],
            "previous_response": load_response(doc),
        },
        durable=True,
    )
    return {"ok": True, "operation": doc.name, "status": doc.status, "external_id": doc.external_id}
