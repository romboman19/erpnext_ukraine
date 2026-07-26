from __future__ import annotations

import re
from html import unescape
from typing import Any

import frappe
from frappe import _
from frappe.core.doctype.role.role import get_info_based_on_role
from frappe.email.doctype.notification.notification import (
    get_assignees,
    get_reference_doctype,
    get_reference_name,
)
from frappe.www.printview import validate_print_permission

from erpnext_ua.integrations.communication.telegram.client import (
    TelegramAPIError,
    TelegramClient,
    is_valid_chat_id,
)
from erpnext_ua.integrations.utils.logger import log_event, sanitize_text
from erpnext_ua.integrations.utils.operations import (
    canonical_hash,
    mark_operation,
    require_new_or_return_success,
    reserve_operation,
)
from erpnext_ua.integrations.utils.security import SALES_MANAGER_ROLES, SALES_ROLES, permitted_doc, require_roles

ALLOWED_RECIPIENT_DOCTYPES = frozenset({"Customer", "Employee", "Supplier", "User"})
TELEGRAM_CAPTION_LIMIT = 1024
DEFAULT_MAX_PDF_BYTES = 20 * 1024 * 1024


def queue_notification_deliveries(alert, doc, context: dict) -> list[str]:
    profile = _load_profile(alert.ua_telegram_bot_profile)
    message = _plain_text(frappe.render_template(alert.message, context))
    maximum = int(profile.max_message_length or 4096)
    _validate_message(message, maximum=maximum, attach_print=bool(alert.attach_print))

    chat_ids = resolve_notification_chat_ids(alert, doc, context)
    if not chat_ids:
        frappe.throw(_("Telegram notification resolved to no chat IDs"))

    reference_doctype = get_reference_doctype(doc)
    reference_name = get_reference_name(doc)
    subject = _render_subject(alert.subject, context, reference_doctype, reference_name)
    occurrence = _notification_occurrence(alert, doc)
    queued: list[str] = []
    for chat_id in chat_ids:
        event_key = canonical_hash(
            {
                "notification": alert.name,
                "occurrence": occurrence,
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
                "chat_id": chat_id,
                "message_sha256": canonical_hash({"text": message}),
            }
        )
        operation_key = f"telegram:notification:{event_key}"
        _enqueue_delivery(
            bot_profile=profile.name,
            chat_id=chat_id,
            message=message,
            subject=subject,
            operation_key=operation_key,
            reference_doctype=reference_doctype,
            reference_name=reference_name,
            attach_print=bool(alert.attach_print),
            print_format=alert.print_format,
            disable_web_page_preview=bool(alert.ua_telegram_disable_web_page_preview),
        )
        queued.append(operation_key)
    return queued


def resolve_notification_chat_ids(alert, doc, context: dict) -> list[str]:
    chat_ids: list[str] = []
    for recipient in alert.recipients or []:
        if recipient.condition and not frappe.safe_eval(recipient.condition, None, context):
            continue

        direct_chat_id = str(recipient.get("ua_telegram_chat_id") or "").strip()
        if direct_chat_id:
            chat_ids.append(direct_chat_id)

        field_path = str(recipient.receiver_by_document_field or "").strip()
        if field_path == "owner":
            chat_ids.extend(_chat_ids_for_party("User", doc.get("owner")))
        elif field_path:
            data_field, child_field = _parse_field_path(field_path)
            if child_field:
                for child in doc.get(child_field) or []:
                    chat_ids.extend(_chat_ids_from_link_field(child, data_field))
            else:
                chat_ids.extend(_chat_ids_from_link_field(doc, data_field))

        if recipient.receiver_by_role:
            chat_ids.extend(
                get_info_based_on_role(
                    recipient.receiver_by_role,
                    "ua_telegram_chat_id",
                    ignore_permissions=True,
                )
            )

    if alert.send_to_all_assignees:
        for user in get_assignees(doc):
            chat_ids.extend(_chat_ids_for_party("User", user))

    normalized = {str(chat_id or "").strip() for chat_id in chat_ids if str(chat_id or "").strip()}
    invalid = sorted(chat_id for chat_id in normalized if not is_valid_chat_id(chat_id))
    if invalid:
        frappe.throw(_("One or more resolved Telegram Chat IDs are invalid"))
    return sorted(normalized)


@frappe.whitelist(methods=["POST"])
def enqueue_telegram_message(
    bot_profile: str,
    chat_id: str,
    message: str,
    idempotency_key: str,
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    subject: str | None = None,
    attach_print: int | bool = 0,
    print_format: str | None = None,
    disable_web_page_preview: int | bool = 1,
) -> dict:
    """Queue one authorized outbound Telegram message without exposing the bot token."""
    require_roles(*SALES_ROLES)
    key = str(idempotency_key or "").strip()
    if not key:
        frappe.throw(_("idempotency_key is required"))
    if len(key) > 200:
        frappe.throw(_("idempotency_key is too long"))

    profile = _load_profile(bot_profile)
    profile.check_permission("read")
    normalized_chat_id = _validated_chat_id(chat_id)
    normalized_message = _plain_text(message)
    should_attach = bool(int(attach_print or 0))
    _validate_message(
        normalized_message,
        maximum=int(profile.max_message_length or 4096),
        attach_print=should_attach,
    )

    if bool(reference_doctype) != bool(reference_name):
        frappe.throw(_("Reference DocType and Reference Name must be provided together"))
    if reference_doctype and reference_name:
        reference = permitted_doc(reference_doctype, reference_name, "read")
        if should_attach:
            validate_print_permission(reference)
    elif should_attach:
        frappe.throw(_("A reference document is required when Attach Print is enabled"))

    operation_key = f"telegram:send:{key}"
    _enqueue_delivery(
        bot_profile=profile.name,
        chat_id=normalized_chat_id,
        message=normalized_message,
        subject=_plain_text(subject or "Telegram message")[:240],
        operation_key=operation_key,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        attach_print=should_attach,
        print_format=print_format,
        disable_web_page_preview=bool(int(disable_web_page_preview or 0)),
    )
    return {"ok": True, "queued": True, "operation_key": operation_key}


@frappe.whitelist(methods=["POST"])
def send_test_message(
    bot_profile: str,
    chat_id: str,
    text: str,
    idempotency_key: str,
) -> dict:
    require_roles(*SALES_MANAGER_ROLES)
    return enqueue_telegram_message(
        bot_profile=bot_profile,
        chat_id=chat_id,
        message=text,
        idempotency_key=idempotency_key,
        subject="Telegram test message",
    )


def deliver_telegram_message(
    *,
    bot_profile: str,
    chat_id: str,
    message: str,
    subject: str,
    operation_key: str,
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    attach_print: bool = False,
    print_format: str | None = None,
    disable_web_page_preview: bool = True,
) -> dict:
    """Worker entry point. It intentionally performs one Telegram side effect only."""
    profile = _load_profile(bot_profile)
    normalized_chat_id = _validated_chat_id(chat_id)
    normalized_message = _plain_text(message)
    _validate_message(
        normalized_message,
        maximum=int(profile.max_message_length or 4096),
        attach_print=bool(attach_print),
    )
    request_summary = {
        "bot_profile": profile.name,
        "chat_id_sha256": canonical_hash({"chat_id": normalized_chat_id}),
        "message_sha256": canonical_hash({"text": normalized_message}),
        "message_length": len(normalized_message),
        "attach_print": bool(attach_print),
        "print_format": print_format or "",
        "reference_doctype": reference_doctype or "",
        "reference_name": reference_name or "",
        "subject_sha256": canonical_hash({"subject": subject}),
        "disable_web_page_preview": bool(disable_web_page_preview),
    }
    reservation = reserve_operation(
        idempotency_key=operation_key,
        integration="telegram",
        operation_type="send_document" if attach_print else "send_message",
        request_payload=request_summary,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
    )
    cached = require_new_or_return_success(reservation)
    if cached is not None:
        return cached

    try:
        pdf_content = _render_pdf(reference_doctype, reference_name, print_format) if attach_print else None
    except Exception:
        error = sanitize_text(frappe.get_traceback())
        mark_operation(reservation.doc, "failed", error=error)
        log_event(
            "telegram",
            "error",
            "Telegram PDF generation failed before provider request",
            reference_doctype=reference_doctype,
            reference_name=reference_name,
            request_payload=request_summary,
            error_trace=error,
        )
        raise

    token = (profile.get_password("bot_token", raise_exception=False) or "").strip()
    client = TelegramClient(token)
    log_event(
        "telegram",
        "queued",
        "Sending Telegram notification",
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        request_payload=request_summary,
    )
    mark_operation(
        reservation.doc,
        "unknown",
        response_payload={"phase": "external_request_in_progress"},
    )

    try:
        if pdf_content is not None:
            response = client.send_document(
                chat_id=normalized_chat_id,
                content=pdf_content,
                filename=_pdf_filename(reference_doctype, reference_name),
                caption=normalized_message,
            )
        else:
            response = client.send_message(
                chat_id=normalized_chat_id,
                text=normalized_message,
                disable_web_page_preview=bool(disable_web_page_preview),
            )
    except TelegramAPIError as exc:
        status = "failed" if exc.definite else "unknown"
        mark_operation(reservation.doc, status, error=str(exc))
        log_event(
            "telegram",
            "error",
            str(exc),
            reference_doctype=reference_doctype,
            reference_name=reference_name,
            request_payload=request_summary,
        )
        raise

    result = response.get("result") if isinstance(response, dict) else None
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if message_id is None:
        error = "Telegram returned success without a message ID"
        mark_operation(reservation.doc, "unknown", error=error)
        log_event(
            "telegram",
            "error",
            error,
            reference_doctype=reference_doctype,
            reference_name=reference_name,
            request_payload=request_summary,
        )
        raise TelegramAPIError(error, definite=False)

    safe_result = {
        "ok": True,
        "message_id": str(message_id),
        "bot_profile": profile.name,
        "chat_id_suffix": _masked_chat_id(normalized_chat_id),
    }
    mark_operation(
        reservation.doc,
        "succeeded",
        external_id=str(message_id),
        response_payload=safe_result,
    )
    log_event(
        "telegram",
        "success",
        "Telegram notification sent",
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        request_payload=request_summary,
        response_payload={"message_id": str(message_id)},
    )
    _create_communication(
        bot_profile=profile.name,
        chat_id=normalized_chat_id,
        subject=subject,
        message=normalized_message,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
    )
    return safe_result


def _enqueue_delivery(**kwargs: Any) -> None:
    operation_key = str(kwargs["operation_key"])
    frappe.enqueue(
        "erpnext_ua.integrations.communication.telegram.service.deliver_telegram_message",
        queue="default",
        timeout=300,
        enqueue_after_commit=True,
        deduplicate=True,
        job_id=f"telegram-{canonical_hash({'operation_key': operation_key})}",
        **kwargs,
    )


def _load_profile(name: str):
    profile = frappe.get_doc("Telegram Bot Profile", name)
    if not profile.enabled:
        frappe.throw(_("Telegram Bot Profile is disabled"))
    if not profile.get_password("bot_token", raise_exception=False):
        frappe.throw(_("Telegram Bot Profile has no token"))
    return profile


def _plain_text(value: str | None) -> str:
    return unescape(frappe.utils.strip_html_tags(str(value or ""))).strip()


def _validate_message(message: str, *, maximum: int, attach_print: bool) -> None:
    if not message:
        frappe.throw(_("Telegram message is required"))
    effective_maximum = min(maximum, TELEGRAM_CAPTION_LIMIT) if attach_print else maximum
    if len(message) > effective_maximum:
        frappe.throw(_("Telegram message exceeds the maximum length ({0})").format(effective_maximum))


def _validated_chat_id(value: str | int | None) -> str:
    chat_id = str(value or "").strip()
    if not is_valid_chat_id(chat_id):
        frappe.throw(_("Invalid Telegram Chat ID"))
    return chat_id


def _parse_field_path(value: str) -> tuple[str, str | None]:
    parts = value.split(",", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (parts[0], None)


def _chat_ids_from_link_field(doc, fieldname: str) -> list[str]:
    field = doc.meta.get_field(fieldname)
    if not field or field.fieldtype != "Link" or field.options not in ALLOWED_RECIPIENT_DOCTYPES:
        frappe.throw(
            _("Telegram recipient field {0} must link to User, Customer, Employee or Supplier").format(fieldname)
        )
    return _chat_ids_for_party(field.options, doc.get(fieldname))


def _chat_ids_for_party(doctype: str, name: str | None) -> list[str]:
    if not name:
        return []
    chat_id = frappe.db.get_value(doctype, name, "ua_telegram_chat_id")
    return [str(chat_id).strip()] if chat_id else []


def _notification_occurrence(alert, doc) -> str:
    if alert.event in {"Days Before", "Days After"}:
        return frappe.utils.nowdate()
    if alert.event in {"Minutes Before", "Minutes After"}:
        return str(alert.datetime_last_run or frappe.utils.now_datetime())
    if alert.event == "Custom":
        return frappe.generate_hash(length=20)
    return str(doc.get("modified") or doc.get("creation") or frappe.utils.now_datetime())


def _render_subject(
    template: str | None,
    context: dict,
    reference_doctype: str,
    reference_name: str,
) -> str:
    subject = str(template or f"{reference_doctype} {reference_name}")
    if "{" in subject:
        subject = frappe.render_template(subject, context)
    return _plain_text(subject)[:240]


def _render_pdf(
    reference_doctype: str | None,
    reference_name: str | None,
    print_format: str | None,
) -> bytes:
    if not reference_doctype or not reference_name:
        frappe.throw(_("Reference document is required for Telegram PDF attachment"))
    doc = frappe.get_doc(reference_doctype, reference_name)
    validate_print_permission(doc)
    content = frappe.get_print(
        reference_doctype,
        reference_name,
        print_format,
        doc=doc,
        as_pdf=True,
    )
    if not isinstance(content, bytes) or not content.startswith(b"%PDF-"):
        frappe.throw(_("Print renderer did not return a valid PDF"))
    configured_limit = int(frappe.conf.get("telegram_max_pdf_bytes", DEFAULT_MAX_PDF_BYTES) or DEFAULT_MAX_PDF_BYTES)
    maximum = max(1024 * 1024, min(configured_limit, 50 * 1024 * 1024))
    if len(content) > maximum:
        frappe.throw(_("Telegram PDF exceeds the configured size limit"))
    return content


def _pdf_filename(reference_doctype: str | None, reference_name: str | None) -> str:
    stem = f"{reference_doctype or 'document'}-{reference_name or 'print'}"
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "-", stem).strip("-.") or "document"
    return f"{sanitized[:100]}.pdf"


def _masked_chat_id(chat_id: str) -> str:
    return f"***{chat_id[-4:]}"


def _create_communication(
    *,
    bot_profile: str,
    chat_id: str,
    subject: str,
    message: str,
    reference_doctype: str | None,
    reference_name: str | None,
) -> None:
    if not reference_doctype or not reference_name:
        return
    try:
        escaped_message = frappe.utils.escape_html(message).replace("\n", "<br>")
        communication = frappe.get_doc(
            {
                "doctype": "Communication",
                "communication_type": "Automated Message",
                "communication_medium": "Telegram",
                "sent_or_received": "Sent",
                "delivery_status": "Sent",
                "subject": subject or "Telegram message",
                "content": escaped_message,
                "text_content": message,
                "recipients": _masked_chat_id(chat_id),
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
                "ua_telegram_bot_profile": bot_profile,
            }
        )
        communication.insert(ignore_permissions=True)
    except Exception:
        log_event(
            "telegram",
            "error",
            "Telegram message sent, but Communication timeline entry failed",
            reference_doctype=reference_doctype,
            reference_name=reference_name,
            error_trace=sanitize_text(frappe.get_traceback()),
        )
