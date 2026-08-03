from __future__ import annotations

import hashlib
import json
from typing import Any

import frappe

from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def hmac_secret(version: str | None = None) -> str:
    configured = frappe.conf.get("ua_gift_certificate_hmac_keys") or {}
    if isinstance(configured, str):
        try:
            configured = json.loads(configured)
        except json.JSONDecodeError:
            configured = {}
    secret = configured.get(version) if version and isinstance(configured, dict) else None
    secret = secret or frappe.conf.get("ua_gift_certificate_hmac_key")
    if not secret:
        raise GiftCertificateError(
            "Gift certificate HMAC key is unavailable",
            "CERT_MODULE_DISABLED",
        )
    return str(secret)


def lock_certificate(name: str):
    frappe.db.sql("select name from `tabUA Gift Certificate` where name=%s for update", name)
    return frappe.get_doc("UA Gift Certificate", name)
