from __future__ import annotations

import hashlib
import secrets
import uuid

import frappe
from frappe import _

from ukrainian_integrations.pbx_sms.sms.turbosms import _send_sms_internal
from ukrainian_integrations.utils.security import SALES_ROLES, permitted_doc, require_roles

CHANNELS = {"SMS": "sms_enabled", "Telegram": "telegram_enabled", "Call": "call_enabled"}
CHANNEL_ALIASES = {
    "sms": "SMS",
    "turbosms": "SMS",
    "telegram": "Telegram",
    "call": "Call",
    "vitalpbx": "Call",
}
CHANNEL_PROVIDERS = {"SMS": "TurboSMS", "Telegram": "Telegram", "Call": "VitalPBX"}
FINAL_STATUSES = {"Verified", "Expired", "Failed", "Cancelled"}
IDENTIFICATION_ROLES = SALES_ROLES + (
    "POS User",
    "POS Cashier",
    "POS Senior Cashier",
    "POS Manager",
    "POS Administrator",
)


def normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) == 10 and digits.startswith("0"):
        digits = "38" + digits
    if len(digits) == 12 and digits.startswith("380"):
        return f"+{digits}"
    frappe.throw(_("Вкажіть український номер у форматі +380XXXXXXXXX"))


def _settings():
    return frappe.get_cached_doc("Customer Identification Settings")


def _setting(settings, fieldname: str, default=None):
    getter = getattr(settings, "get", None)
    value = getter(fieldname) if callable(getter) else getattr(settings, fieldname, None)
    return default if value in (None, "") else value


def _canonical_channel(channel: str | None) -> str:
    raw = str(channel or "").strip()
    return CHANNEL_ALIASES.get(raw.lower(), raw)


def _select_channel(
    settings,
    requested_channel: str | None = None,
    *,
    for_pos: bool = False,
) -> str:
    """Resolve channel policy without performing I/O; POS can be locked by settings."""
    fieldname = "pos_channel" if for_pos else "default_channel"
    configured = _canonical_channel(_setting(settings, fieldname, "SMS")) or "SMS"
    if for_pos and not bool(_setting(settings, "allow_pos_channel_selection", 0)):
        return configured
    return _canonical_channel(requested_channel) or configured


def _hash_code(request_token: str, code: str) -> str:
    return hashlib.sha256(f"{request_token}:{code}".encode()).hexdigest()


def _is_expired(doc) -> bool:
    return bool(
        doc.expires_at
        and frappe.utils.now_datetime() > frappe.utils.get_datetime(doc.expires_at)
    )


def _expire_if_needed(doc):
    if doc.status == "Pending" and _is_expired(doc):
        doc.status = "Expired"
        doc.save(ignore_permissions=True)
    return doc


def _lock_request(request_id: str) -> None:
    frappe.db.sql(
        "SELECT name FROM `tabCustomer Identification Request` WHERE name = %s FOR UPDATE",
        (request_id,),
    )


def _get_request(request_id: str):
    doc = frappe.get_doc("Customer Identification Request", request_id)
    roles = set(frappe.get_roles())
    if (
        doc.initiated_by != frappe.session.user
        and frappe.session.user != "Administrator"
        and "System Manager" not in roles
    ):
        frappe.throw(
            _("Немає доступу до цього запиту ідентифікації"),
            frappe.PermissionError,
        )
    return doc


def _find_customer(phone: str) -> str | None:
    digits = phone.replace("+", "")
    meta = frappe.get_meta("Customer")
    for field in (field for field in ("mobile_no", "phone") if meta.has_field(field)):
        for variant in (phone, digits, digits[-10:]):
            customer = frappe.db.get_value(
                "Customer",
                {field: ("like", f"%{variant}%")},
                "name",
            )
            if customer:
                return customer
    return None


def _customer_payload(customer: str | None) -> dict | None:
    if not customer:
        return None
    doc = permitted_doc("Customer", customer, "read")
    meta = frappe.get_meta("Customer")
    fields = ["name", "customer_name"]
    fields.extend(
        field
        for field in (
            "mobile_no",
            "phone",
            "email_id",
            "ua_last_name",
            "ua_first_name",
            "ua_middle_name",
            "ua_gender",
            "ua_date_of_birth",
            "ua_city",
            "ua_pos_comment",
        )
        if meta.has_field(field)
    )
    payload = {field: doc.get(field) for field in fields}
    payload.setdefault("mobile_no", None)
    payload.setdefault("phone", None)
    return payload


def _rate_limit(phone: str, settings) -> None:
    window_start = frappe.utils.add_to_date(
        frappe.utils.now_datetime(),
        minutes=-10,
    )
    count = frappe.db.count(
        "Customer Identification Request",
        filters={"phone": phone, "creation": (">=", window_start)},
    )
    if count >= int(settings.rate_limit_per_10_min or 5):
        frappe.throw(
            _("Забагато спроб. Повторіть ідентифікацію пізніше."),
            frappe.RateLimitExceededError,
        )


def _public_result(doc, *, debug_code: str | None = None) -> dict:
    settings = _settings()
    result = {
        "request_id": doc.name,
        "request_token": doc.request_token,
        "channel": doc.channel,
        "phone": doc.phone,
        "status": doc.status,
        "expires_at": str(doc.expires_at),
        "customer": _customer_payload(doc.customer) if doc.status == "Verified" else None,
        "instructions": doc.instructions or "",
    }
    if doc.channel == "Telegram" and settings.telegram_bot_username:
        result["deep_link"] = (
            f"https://t.me/{settings.telegram_bot_username}?start=cid_{doc.request_token}"
        )
    if (
        debug_code
        and settings.test_mode
        and "System Manager" in frappe.get_roles()
    ):
        result["debug_code"] = debug_code
    return result


def _channel_enabled(channel: str, settings) -> bool:
    return channel in CHANNELS and bool(_setting(settings, CHANNELS[channel], 0))


def _validate_reference(
    reference_doctype: str | None,
    reference_name: str | None,
) -> tuple[str, str]:
    doctype = str(reference_doctype or "").strip()
    name = str(reference_name or "").strip()
    if bool(doctype) != bool(name):
        frappe.throw(_("Reference type and name must be provided together"))
    if doctype:
        permitted_doc(doctype, name, "read")
    return doctype, name


def _existing_pending(
    *,
    channel: str,
    phone: str,
    reference_doctype: str,
    reference_name: str,
):
    filters = {
        "channel": channel,
        "phone": phone,
        "status": "Pending",
        "initiated_by": frappe.session.user,
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
    }
    name = frappe.db.get_value(
        "Customer Identification Request",
        filters,
        "name",
        order_by="creation desc",
    )
    if not name:
        return None
    doc = _expire_if_needed(frappe.get_doc("Customer Identification Request", name))
    return doc if doc.status == "Pending" else None


@frappe.whitelist()
def get_config() -> dict:
    require_roles(*IDENTIFICATION_ROLES)
    settings = _settings()
    channels = [channel for channel in CHANNELS if _channel_enabled(channel, settings)]
    default_channel = _select_channel(settings)
    pos_channel = _select_channel(settings, for_pos=True)
    return {
        "enabled": bool(settings.enabled),
        "channels": channels,
        "channel_options": [
            {
                "name": channel,
                "label": {
                    "SMS": _("SMS-код"),
                    "Telegram": _("Telegram-бот"),
                    "Call": _("Контрольний дзвінок"),
                }[channel],
                "provider": CHANNEL_PROVIDERS[channel],
            }
            for channel in channels
        ],
        "default_channel": default_channel,
        "pos_channel": pos_channel,
        "allow_pos_channel_selection": bool(
            _setting(settings, "allow_pos_channel_selection", 0)
        ),
        "ttl_minutes": int(settings.ttl_minutes or 5),
        "call_verification_number": settings.call_verification_number or "",
        "telegram_bot_username": settings.telegram_bot_username or "",
        "test_mode": bool(settings.test_mode),
    }


@frappe.whitelist()
def find_by_phone(phone: str) -> dict:
    """Return only existence before verification; do not disclose customer PII."""
    require_roles(*IDENTIFICATION_ROLES)
    normalized = normalize_phone(phone)
    customer = _find_customer(normalized)
    if customer:
        permitted_doc("Customer", customer, "read")
    return {
        "phone": normalized,
        "customer": {"exists": True} if customer else None,
    }


@frappe.whitelist(methods=["POST"])
def begin(
    channel: str | None = None,
    phone: str | None = None,
    reference_doctype: str | None = None,
    reference_name: str | None = None,
) -> dict:
    require_roles(*IDENTIFICATION_ROLES)
    settings = _settings()
    return _begin_request(
        settings,
        _select_channel(settings, channel),
        phone,
        reference_doctype,
        reference_name,
    )


@frappe.whitelist(methods=["POST"])
def begin_pos(
    phone: str,
    channel: str | None = None,
    reference_doctype: str | None = None,
    reference_name: str | None = None,
) -> dict:
    """Start POS verification using the admin-controlled POS channel policy."""
    require_roles(*IDENTIFICATION_ROLES)
    settings = _settings()
    return _begin_request(
        settings,
        _select_channel(settings, channel, for_pos=True),
        phone,
        reference_doctype,
        reference_name,
    )


def _begin_request(
    settings,
    channel: str,
    phone: str | None,
    reference_doctype: str | None,
    reference_name: str | None,
) -> dict:
    if not settings.enabled:
        frappe.throw(_("Модуль ідентифікації покупця вимкнений"))
    normalized_channel = _canonical_channel(channel)
    if not _channel_enabled(normalized_channel, settings):
        frappe.throw(_("Канал {0} не налаштований").format(normalized_channel))
    normalized_phone = normalize_phone(phone)
    reference_doctype, reference_name = _validate_reference(
        reference_doctype,
        reference_name,
    )

    existing = _existing_pending(
        channel=normalized_channel,
        phone=normalized_phone,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
    )
    if existing:
        return _public_result(existing)

    _rate_limit(normalized_phone, settings)
    request_token = uuid.uuid4().hex
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = frappe.utils.add_to_date(
        frappe.utils.now_datetime(),
        minutes=int(settings.ttl_minutes or 5),
        as_datetime=True,
    )
    instructions = {
        "SMS": _("Введіть шестизначний код із SMS."),
        "Telegram": _("Відкрийте Telegram-бота та надішліть свій контакт."),
        "Call": _("Зателефонуйте з цього номера на {0}.").format(
            settings.call_verification_number or "номер магазину"
        ),
    }[normalized_channel]
    doc = frappe.get_doc(
        {
            "doctype": "Customer Identification Request",
            "request_token": request_token,
            "channel": normalized_channel,
            "phone": normalized_phone,
            "status": "Pending",
            "code_hash": (
                _hash_code(request_token, code)
                if normalized_channel == "SMS"
                else ""
            ),
            "expires_at": expires_at,
            "customer": _find_customer(normalized_phone),
            "initiated_by": frappe.session.user,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "instructions": instructions,
        }
    ).insert(ignore_permissions=True)

    if normalized_channel == "SMS" and not settings.test_mode:
        text = (
            settings.sms_template
            or "Код підтвердження: {code}. Дійсний {minutes} хв."
        ).format(
            code=code,
            minutes=int(settings.ttl_minutes or 5),
        )
        sms_key = f"customer-identification:{doc.name}"
        try:
            response = _send_sms_internal(
                normalized_phone,
                text,
                idempotency_key=sms_key,
                sender=settings.sms_sender or None,
            )
        except Exception:
            operation_status = frappe.db.get_value(
                "UA Integration Operation",
                {"idempotency_key": f"turbosms:send:{sms_key}"},
                "status",
            )
            if operation_status == "failed":
                doc.status = "Failed"
                doc.save(ignore_permissions=True)
                frappe.db.commit()
            raise
        doc.external_reference = (
            f"TurboSMS:{response.get('message_id')}"
            if response.get("message_id")
            else "TurboSMS"
        )
        doc.save(ignore_permissions=True)

    frappe.db.commit()
    return _public_result(
        doc,
        debug_code=code if normalized_channel == "SMS" else None,
    )


def _mark_verified(
    doc,
    *,
    external_reference: str | None = None,
    telegram_user_id: str | None = None,
):
    doc.status = "Verified"
    doc.customer = doc.customer or _find_customer(doc.phone)
    doc.verified_at = frappe.utils.now_datetime()
    doc.verified_by = (
        frappe.session.user if frappe.session.user != "Guest" else "Administrator"
    )
    if external_reference:
        doc.external_reference = external_reference[:1000]
    if telegram_user_id:
        doc.telegram_user_id = str(telegram_user_id)[:140]
    doc.save(ignore_permissions=True)
    frappe.publish_realtime(
        "customer_identification_verified",
        {"request_id": doc.name, "customer": doc.customer, "phone": doc.phone},
        user=doc.initiated_by,
        after_commit=True,
    )
    return doc


@frappe.whitelist(methods=["POST"])
def confirm(request_id: str, code: str | None = None) -> dict:
    require_roles(*IDENTIFICATION_ROLES)
    request = _get_request(request_id)
    _lock_request(request.name)
    doc = _expire_if_needed(_get_request(request.name))
    if doc.status in FINAL_STATUSES:
        return _public_result(doc)
    if doc.channel != "SMS":
        return _public_result(doc)
    if int(doc.attempts or 0) >= int(_settings().max_attempts or 5):
        doc.status = "Failed"
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.throw(_("Ліміт спроб вичерпано"))
    doc.attempts = int(doc.attempts or 0) + 1
    supplied_hash = _hash_code(doc.request_token, str(code or "").strip())
    if not secrets.compare_digest(doc.code_hash or "", supplied_hash):
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.throw(_("Неправильний код підтвердження"))
    _mark_verified(doc)
    frappe.db.commit()
    return _public_result(doc)


@frappe.whitelist()
def get_status(request_id: str) -> dict:
    require_roles(*IDENTIFICATION_ROLES)
    doc = _expire_if_needed(_get_request(request_id))
    return _public_result(doc)


@frappe.whitelist(methods=["POST"])
def cancel(request_id: str) -> dict:
    require_roles(*IDENTIFICATION_ROLES)
    request = _get_request(request_id)
    _lock_request(request.name)
    doc = _get_request(request.name)
    if doc.status == "Pending":
        doc.status = "Cancelled"
        doc.save(ignore_permissions=True)
    return _public_result(doc)


@frappe.whitelist(methods=["POST"])
def quick_create(
    request_id: str,
    customer_name: str | None = None,
    last_name: str | None = None,
    first_name: str | None = None,
    middle_name: str | None = None,
    gender: str | None = None,
    date_of_birth: str | None = None,
    comment: str | None = None,
    city: str | None = None,
    email: str | None = None,
) -> dict:
    require_roles(*IDENTIFICATION_ROLES)
    request = _get_request(request_id)
    _lock_request(request.name)
    request = _expire_if_needed(_get_request(request.name))
    if request.status != "Verified":
        frappe.throw(
            _("Спочатку підтвердьте номер покупця"),
            frappe.PermissionError,
        )
    if request.customer:
        return _customer_payload(request.customer)

    existing = _find_customer(request.phone)
    if existing:
        request.customer = existing
        request.save(ignore_permissions=True)
        return _customer_payload(existing)

    if not frappe.has_permission("Customer", "create"):
        frappe.throw(_("Немає права створювати покупців"), frappe.PermissionError)
    if not frappe.has_permission("Contact", "create"):
        frappe.throw(_("Немає права створювати контакти"), frappe.PermissionError)

    last = str(last_name or "").strip()[:80]
    first = str(first_name or "").strip()[:80]
    middle = str(middle_name or "").strip()[:80]
    legacy_name = str(customer_name or "").strip()[:160]
    if not first and not last and legacy_name:
        parts = legacy_name.split(maxsplit=1)
        last = parts[0]
        first = parts[1] if len(parts) > 1 else parts[0]
    if not first or not last:
        frappe.throw(_("Вкажіть прізвище та ім’я покупця"))

    normalized_email = str(email or "").strip()[:320]
    if normalized_email:
        frappe.utils.validate_email_address(normalized_email, throw=True)
    full_name = " ".join(part for part in (last, first, middle) if part)
    customer_meta = frappe.get_meta("Customer")
    payload = {
        "doctype": "Customer",
        "customer_name": full_name,
        "customer_type": "Individual",
        "customer_group": (
            frappe.db.get_single_value("Selling Settings", "customer_group")
            or "Individual"
        ),
        "territory": (
            frappe.db.get_single_value("Selling Settings", "territory")
            or "Ukraine"
        ),
    }
    for fieldname, value in {
        "mobile_no": request.phone,
        "email_id": normalized_email or None,
        "ua_last_name": last,
        "ua_first_name": first,
        "ua_middle_name": middle or None,
        "ua_gender": gender or None,
        "ua_date_of_birth": date_of_birth or None,
        "ua_city": str(city or "").strip()[:140] or None,
        "ua_pos_comment": str(comment or "").strip()[:1000] or None,
    }.items():
        if value is not None and customer_meta.has_field(fieldname):
            payload[fieldname] = value
    customer_doc = frappe.get_doc(payload).insert()

    contact_meta = frappe.get_meta("Contact")
    contact_payload = {
        "doctype": "Contact",
        "first_name": first,
        "last_name": last,
        "links": [
            {
                "link_doctype": "Customer",
                "link_name": customer_doc.name,
            }
        ],
        "phone_nos": [
            {
                "phone": request.phone,
                "is_primary_phone": 1,
                "is_primary_mobile_no": 1,
            }
        ],
    }
    for fieldname, value in {
        "middle_name": middle or None,
        "gender": gender or None,
        "date_of_birth": date_of_birth or None,
    }.items():
        if value is not None and contact_meta.has_field(fieldname):
            contact_payload[fieldname] = value
    if normalized_email:
        contact_payload["email_ids"] = [
            {
                "email_id": normalized_email,
                "is_primary": 1,
            }
        ]
    contact = frappe.get_doc(contact_payload).insert()
    if customer_meta.has_field("customer_primary_contact"):
        customer_doc.customer_primary_contact = contact.name
        customer_doc.save()

    request.customer = customer_doc.name
    request.save(ignore_permissions=True)
    frappe.db.commit()
    return _customer_payload(customer_doc.name)


def confirm_inbound_call(
    phone: str,
    call_id: str,
    status: str,
    duration: int = 0,
) -> str | None:
    settings = _settings()
    if not settings.enabled or not settings.call_enabled:
        return None
    if str(status or "").lower() not in {"answered", "completed"}:
        return None
    try:
        normalized_phone = normalize_phone(phone)
    except Exception:
        return None
    rows = frappe.get_all(
        "Customer Identification Request",
        filters={
            "channel": "Call",
            "phone": normalized_phone,
            "status": "Pending",
        },
        fields=["name"],
        order_by="creation desc",
        limit=1,
    )
    if not rows:
        return None
    _lock_request(rows[0].name)
    doc = _expire_if_needed(
        frappe.get_doc("Customer Identification Request", rows[0].name)
    )
    if doc.status != "Pending":
        return None
    _mark_verified(
        doc,
        external_reference=f"VitalPBX:{str(call_id)[:140]};duration={max(0, int(duration or 0))}",
    )
    return doc.name


def expire_pending() -> dict:
    names = frappe.get_all(
        "Customer Identification Request",
        filters={
            "status": "Pending",
            "expires_at": ("<", frappe.utils.now_datetime()),
        },
        pluck="name",
        limit=500,
    )
    for name in names:
        frappe.db.set_value(
            "Customer Identification Request",
            name,
            "status",
            "Expired",
            update_modified=False,
        )
    return {"expired": len(names)}
