from __future__ import annotations

import frappe

from erpnext_ua.ua_fiscal import orchestration
from erpnext_ua.ua_fiscal.sales_invoice import _register_for_invoice, fiscalize_invoice

COMPLETED_RECEIPT_STATUSES = {"Fiscalized", "Offline"}
RETRYABLE_JOB_STATUSES = {"Pending", "Processing", "Uncertain"}
MAX_AUTOMATIC_ATTEMPTS = 10


class FiscalizationPending(Exception):
    pass


class FiscalizationManualReview(Exception):
    pass


def ensure_sales_invoice_job(
    sales_invoice,
    *,
    cash_register: str | None = None,
    pos_order: str | None = None,
    cash_desk: str | None = None,
):
    invoice = (
        sales_invoice
        if getattr(sales_invoice, "doctype", None) == "Sales Invoice"
        else frappe.get_doc("Sales Invoice", sales_invoice)
    )
    if int(invoice.docstatus or 0) != 1:
        frappe.throw(f"Sales Invoice {invoice.name} must be submitted before fiscalization")
    cash_register = cash_register or _register_for_invoice(invoice)
    if not cash_register:
        return None
    _validate_register_company(invoice, cash_register)
    key = f"sales-invoice:{cash_register}:{invoice.name}"[:140]
    existing = frappe.db.get_value("PRRO Fiscalization Job", {"idempotency_key": key}, "name")
    if existing:
        _fill_source_context(existing, pos_order=pos_order, cash_desk=cash_desk)
        return frappe.get_doc("PRRO Fiscalization Job", existing)
    try:
        return frappe.get_doc(
            {
                "doctype": "PRRO Fiscalization Job",
                "sales_invoice": invoice.name,
                "pos_order": pos_order or invoice.get("ua_pos_order"),
                "cash_desk": cash_desk,
                "cash_register": cash_register,
                "status": "Pending",
                "idempotency_key": key,
            }
        ).insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        return frappe.get_doc("PRRO Fiscalization Job", key)


def _validate_register_company(invoice, cash_register: str) -> None:
    company = frappe.db.get_value("PRRO Cash Register", cash_register, "company")
    if company != invoice.company:
        frappe.throw(
            f"Каса ПРРО {cash_register} належить компанії {company}, "
            f"але Sales Invoice {invoice.name} — компанії {invoice.company}"
        )


def _fill_source_context(job_name: str, *, pos_order: str | None, cash_desk: str | None) -> None:
    values = {}
    if pos_order and not frappe.db.get_value("PRRO Fiscalization Job", job_name, "pos_order"):
        values["pos_order"] = pos_order
    if cash_desk and not frappe.db.get_value("PRRO Fiscalization Job", job_name, "cash_desk"):
        values["cash_desk"] = cash_desk
    if values:
        frappe.db.set_value("PRRO Fiscalization Job", job_name, values, update_modified=False)


def enqueue_job(job_name: str) -> None:
    frappe.enqueue(
        "erpnext_ua.ua_fiscal.outbox.process_job",
        queue="short",
        enqueue_after_commit=True,
        job_id=f"prro-outbox:{job_name}",
        fiscal_job=job_name,
    )


def process_job(fiscal_job: str, client=None, *, raise_on_error: bool = False) -> dict:
    with frappe.cache.lock(
        f"erpnext_ua:prro-outbox:{fiscal_job}",
        timeout=600,
        blocking_timeout=1,
    ):
        job = frappe.get_doc("PRRO Fiscalization Job", fiscal_job)
        if job.status == "Completed":
            return job.as_dict()
        _mark_processing(job)
        try:
            receipt_name = _deliver(job, client=client)
            return complete_job(job.name, receipt_name)
        except Exception as exc:
            _record_failure(job.name, exc)
            if raise_on_error:
                raise
            return frappe.get_doc("PRRO Fiscalization Job", job.name).as_dict()


def _mark_processing(job) -> None:
    frappe.db.set_value(
        "PRRO Fiscalization Job",
        job.name,
        {
            "status": "Processing",
            "attempt_count": int(job.attempt_count or 0) + 1,
            "last_attempt_at": frappe.utils.now_datetime(),
            "next_attempt_at": None,
        },
        update_modified=False,
    )
    frappe.db.commit()
    job.reload()


def _deliver(job, *, client=None) -> str:
    receipt = _active_receipt(job)
    if receipt:
        receipt = orchestration.resume_pending_sale_receipt(receipt.name, client=client)
        if receipt.status in COMPLETED_RECEIPT_STATUSES:
            return receipt.name
        if receipt.status == "Uncertain":
            raise FiscalizationPending(f"Доставка чека {receipt.name} залишається невизначеною")
        if receipt.status == "Error":
            raise FiscalizationManualReview(receipt.error_message or f"Чек {receipt.name} відхилено")
        if receipt.status != "Cancelled":
            raise FiscalizationPending(f"Чек {receipt.name} має незавершений статус {receipt.status}")
    receipt_name = _run_source_handler(job, client=client)
    receipt = frappe.get_doc("PRRO Receipt", receipt_name)
    if receipt.status not in COMPLETED_RECEIPT_STATUSES:
        raise FiscalizationPending(f"Чек {receipt.name} має незавершений статус {receipt.status}")
    return receipt.name


def _active_receipt(job):
    name = frappe.db.get_value(
        "PRRO Receipt",
        {
            "sales_invoice": job.sales_invoice,
            "cash_register": job.cash_register,
            "status": ("!=", "Cancelled"),
        },
        "name",
        order_by="local_number desc, creation desc",
    )
    return frappe.get_doc("PRRO Receipt", name) if name else None


def _run_source_handler(job, *, client=None) -> str:
    if job.pos_order:
        if not job.cash_desk:
            raise FiscalizationManualReview(f"Outbox {job.name} has no POS Cash Desk")
        from erpnext_ua.ua_pos.api import _fiscalize

        return _fiscalize(
            frappe.get_doc("POS Order", job.pos_order),
            frappe.get_doc("POS Cash Desk", job.cash_desk),
            frappe.get_doc("Sales Invoice", job.sales_invoice),
        )
    return fiscalize_invoice(
        job.sales_invoice,
        client=client,
        cash_register=job.cash_register,
    )


def complete_job(job_name: str, receipt_name: str) -> dict:
    receipt = frappe.get_doc("PRRO Receipt", receipt_name)
    if receipt.status not in COMPLETED_RECEIPT_STATUSES:
        raise FiscalizationPending(f"Чек {receipt.name} ще не завершено")
    frappe.db.set_value(
        "PRRO Fiscalization Job",
        job_name,
        {
            "status": "Completed",
            "receipt": receipt.name,
            "completed_at": frappe.utils.now_datetime(),
            "next_attempt_at": None,
            "error_message": None,
        },
        update_modified=False,
    )
    frappe.db.commit()
    job = frappe.get_doc("PRRO Fiscalization Job", job_name)
    _sync_ecommerce_state(job, receipt.status)
    return job.as_dict()


def _record_failure(job_name: str, exc: Exception) -> None:
    job = frappe.get_doc("PRRO Fiscalization Job", job_name)
    receipt = _active_receipt(job)
    if isinstance(exc, FiscalizationManualReview) or int(job.attempt_count or 0) >= MAX_AUTOMATIC_ATTEMPTS:
        status = "Manual Review"
        next_attempt = None
    else:
        status = "Uncertain" if receipt and receipt.status == "Uncertain" else "Pending"
        delay = min(300, 5 * (2 ** max(0, int(job.attempt_count or 1) - 1)))
        next_attempt = frappe.utils.add_to_date(None, seconds=delay, as_datetime=True)
    frappe.db.set_value(
        "PRRO Fiscalization Job",
        job.name,
        {
            "status": status,
            "receipt": receipt.name if receipt else None,
            "next_attempt_at": next_attempt,
            "error_message": str(exc)[:1000],
        },
        update_modified=False,
    )
    frappe.db.commit()
    _sync_ecommerce_state(job, "Uncertain" if status == "Uncertain" else "Error", str(exc))
    frappe.log_error(frappe.get_traceback(), f"PRRO outbox {job.name}")


def _sync_ecommerce_state(job, status: str, error: str = "") -> None:
    if not frappe.db.has_column("Sales Invoice", "ua_ecommerce_channel"):
        return
    if not frappe.db.get_value("Sales Invoice", job.sales_invoice, "ua_ecommerce_channel"):
        return
    from erpnext_ua.ua_fiscal.ecommerce import _set_fiscal_state

    _set_fiscal_state(job.sales_invoice, status, error)
    frappe.db.commit()


def recover_due_jobs(limit: int = 50) -> dict:
    now = frappe.utils.now_datetime()
    names = frappe.get_all(
        "PRRO Fiscalization Job",
        filters={"status": ("in", tuple(RETRYABLE_JOB_STATUSES))},
        fields=["name", "next_attempt_at"],
        order_by="next_attempt_at asc, creation asc",
        limit=max(1, min(int(limit or 50), 200)),
    )
    processed = 0
    pos_processed = False
    for row in names:
        if row.next_attempt_at and frappe.utils.get_datetime(row.next_attempt_at) > now:
            continue
        try:
            result = process_job(row.name)
            processed += 1
            pos_processed = pos_processed or bool(result.get("pos_order"))
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"PRRO outbox scheduler {row.name}")
    if pos_processed:
        from erpnext_ua.ua_pos.api import recover_pos_fiscal_pending

        recover_pos_fiscal_pending()
    return {"checked": len(names), "processed": processed}
