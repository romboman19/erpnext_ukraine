from __future__ import annotations

import json

import frappe

from erpnext_ua.ua_loyalty.context import service_write
from erpnext_ua.ua_loyalty.domain.money import ZERO, decimal, money
from erpnext_ua.ua_loyalty.services.account_service import account_for, lock_account
from erpnext_ua.ua_loyalty.services.ledger_service import append_ledger, append_metric
from erpnext_ua.ua_loyalty.services.reconciliation_service import reconcile_account


def run_import(batch_name: str) -> dict:
    batch = frappe.get_doc("UA Loyalty Import Batch", batch_name)
    if batch.status not in {"DRAFT", "FAILED"}:
        frappe.throw("Import batch уже виконано або виконується")
    rows = json.loads(batch.source_data)
    errors = _validate_rows(rows)
    if errors:
        _finish(batch, "FAILED", errors, [], ZERO, ZERO)
        return _result(batch, errors, [])
    if batch.dry_run:
        _finish(
            batch,
            "DRY_RUN_COMPLETE",
            [],
            [],
            sum((money(row.get("marketing_balance")) for row in rows), ZERO),
            sum((money(row.get("metric_balance")) for row in rows), ZERO),
        )
        return _result(batch, [], [])

    with service_write():
        batch.status = "RUNNING"
        batch.started_at = frappe.utils.now_datetime()
        batch.save(ignore_permissions=True)
    accounts = []
    marketing_total = ZERO
    metric_total = ZERO
    for index, row in enumerate(rows, 1):
        account = account_for(row["customer"], row["scope"], create=True, program=row.get("program"))
        account = lock_account(account.name)
        marketing = money(row.get("marketing_balance"))
        metric = money(row.get("metric_balance"))
        if marketing:
            append_ledger(
                account,
                entry_type="OPENING_CREDIT" if marketing > ZERO else "OPENING_DEBIT",
                active_delta=marketing,
                idempotency_key=f"import:{batch.name}:{index}:balance",
                source_doctype=batch.doctype,
                source_name=batch.name,
                values={"reason_code": "OPENING_IMPORT", "lot_kind": "OPENING"},
            )
        if metric:
            append_metric(
                account,
                entry_type="OPENING_AMOUNT",
                delta=metric,
                idempotency_key=f"import:{batch.name}:{index}:metric",
                source_doctype=batch.doctype,
                source_name=batch.name,
                values={"reason_code": "OPENING_IMPORT"},
            )
        accounts.append(reconcile_account(account.name))
        marketing_total += marketing
        metric_total += metric
    _finish(batch, "COMPLETED", [], accounts, marketing_total, metric_total)
    return _result(batch, [], accounts)


def _validate_rows(rows: list[dict]) -> list[dict]:
    errors = []
    seen = set()
    for index, row in enumerate(rows, 1):
        key = (row.get("customer"), row.get("scope"))
        messages = []
        if not all(key):
            messages.append("customer і scope обов’язкові")
        elif key in seen:
            messages.append("дублікат customer/scope у batch")
        seen.add(key)
        if row.get("customer") and not frappe.db.exists("Customer", row["customer"]):
            messages.append("Customer не існує")
        if row.get("scope") and not frappe.db.exists("UA Loyalty Scope", row["scope"]):
            messages.append("Scope не існує")
        for fieldname in ("marketing_balance", "metric_balance"):
            try:
                decimal(row.get(fieldname))
            except TypeError:
                messages.append(f"{fieldname} не є числом")
            except ValueError:
                messages.append(f"{fieldname} не є числом")
        if messages:
            errors.append({"row": index, "messages": messages})
    return errors


def _finish(batch, status, errors, accounts, marketing_total, metric_total):
    with service_write():
        batch.status = status
        batch.started_at = batch.started_at or frappe.utils.now_datetime()
        batch.finished_at = frappe.utils.now_datetime()
        batch.success_count = 0 if status == "FAILED" else batch.row_count
        batch.error_count = len(errors)
        batch.marketing_total = marketing_total
        batch.metric_total = metric_total
        batch.errors_json = frappe.as_json(errors)
        batch.reconciliation_json = frappe.as_json(accounts)
        batch.save(ignore_permissions=True)


def _result(batch, errors, accounts):
    return {
        "batch": batch.name,
        "status": batch.status,
        "row_count": batch.row_count,
        "success_count": batch.success_count,
        "error_count": batch.error_count,
        "errors": errors,
        "reconciliation": accounts,
    }
