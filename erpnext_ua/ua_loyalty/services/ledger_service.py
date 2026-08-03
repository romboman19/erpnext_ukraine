from __future__ import annotations

from decimal import Decimal
from typing import Any

import frappe

from erpnext_ua.ua_loyalty.constants import ENTRY_ACTIVE_CREDITS, ENTRY_PENDING_CREDITS
from erpnext_ua.ua_loyalty.context import service_write
from erpnext_ua.ua_loyalty.domain.balances import apply_credit
from erpnext_ua.ua_loyalty.domain.money import ZERO, decimal, money
from erpnext_ua.ua_loyalty.domain.snapshots import payload_hash
from erpnext_ua.ua_loyalty.exceptions import LoyaltyConflict

from .account_service import update_cached_balances, update_metric


def append_ledger(
    account,
    *,
    entry_type: str,
    active_delta: Decimal = ZERO,
    pending_delta: Decimal = ZERO,
    idempotency_key: str,
    source_doctype: str,
    source_name: str,
    values: dict[str, Any] | None = None,
):
    values = values or {}
    payload = {
        "account": account.name,
        "entry_type": entry_type,
        "active_delta": str(money(active_delta)),
        "pending_delta": str(money(pending_delta)),
        "source_doctype": source_doctype,
        "source_name": source_name,
        "source_row_name": values.get("source_row_name"),
        "rule_snapshot_hash": values.get("rule_snapshot_hash"),
    }
    fingerprint = payload_hash(payload)
    existing = frappe.db.get_value(
        "UA Loyalty Ledger Entry", {"idempotency_key": idempotency_key}, ["name", "payload_hash"], as_dict=True
    )
    if existing:
        if existing.payload_hash != fingerprint:
            raise LoyaltyConflict("Idempotency key має інший payload", "LOYALTY_IDEMPOTENCY_CONFLICT")
        return frappe.get_doc("UA Loyalty Ledger Entry", existing.name)

    active_delta = money(active_delta)
    pending_delta = money(pending_delta)
    before = money(account.marketing_balance)
    pending_before = money(account.pending_balance)
    state = update_cached_balances(account, active_delta=active_delta, pending_delta=pending_delta)
    doc_values = {
        "doctype": "UA Loyalty Ledger Entry",
        "account": account.name,
        "scope": account.scope,
        "program": account.program,
        "program_version": values.get("program_version"),
        "entry_type": entry_type,
        "posting_datetime": values.get("posting_datetime") or frappe.utils.now_datetime(),
        "effective_datetime": values.get("effective_datetime") or frappe.utils.now_datetime(),
        "active_delta": active_delta,
        "pending_delta": pending_delta,
        "source_doctype": source_doctype,
        "source_name": source_name,
        "idempotency_key": idempotency_key,
        "payload_hash": fingerprint,
        "balance_before": before,
        "balance_after": state.marketing,
        "pending_before": pending_before,
        "pending_after": state.pending,
        "created_by_service": "ledger_service",
    }
    for fieldname in (
        "expires_at",
        "source_row_name",
        "pos_order",
        "sales_invoice",
        "company",
        "fop_profile",
        "pos_cash_desk",
        "warehouse",
        "branch",
        "sales_channel",
        "issuer_company",
        "redeemer_company",
        "original_entry",
        "reversal_of",
        "rule_snapshot_hash",
        "metadata_json",
        "reason_code",
    ):
        if fieldname in values:
            doc_values[fieldname] = values[fieldname]
    with service_write():
        entry = frappe.get_doc(doc_values).insert(ignore_permissions=True)
    if entry_type == "REDEEM" and active_delta < ZERO:
        _consume_credit_lots(account, entry, -active_delta)
    elif active_delta < ZERO and entry_type not in {"EXPIRE", "MANUAL_DEBIT", "OPENING_DEBIT"}:
        _reduce_origin_lot(entry, -active_delta, values)
    account.last_ledger_entry = entry.name
    with service_write():
        account.save(ignore_permissions=True)
    _create_credit_lot(account, entry, entry_type, active_delta, pending_delta, values)
    return entry


def _consume_credit_lots(account, entry, amount):
    mode = frappe.db.get_value("UA Loyalty Program", account.program, "credit_consumption_mode")
    if mode == "AGGREGATE_BALANCE":
        return
    lots = frappe.get_all(
        "UA Loyalty Bonus Lot",
        filters={"account": account.name, "status": "ACTIVE", "available_amount": (">", 0)},
        fields=["name", "available_amount", "effective_datetime", "expires_at"],
        limit=10000,
    )
    if mode == "EARLIEST_EXPIRY_FIRST":
        lots.sort(
            key=lambda lot: (
                lot.expires_at is None,
                lot.expires_at or frappe.utils.get_datetime("2999-12-31"),
                lot.effective_datetime,
                lot.name,
            )
        )
    else:
        lots.sort(key=lambda lot: (lot.effective_datetime, lot.name))
    remaining = money(amount)
    for sequence, row in enumerate(lots, 1):
        if remaining <= ZERO:
            break
        lot = frappe.get_doc("UA Loyalty Bonus Lot", row.name)
        consumed = min(remaining, money(lot.available_amount))
        with service_write():
            lot.available_amount = money(decimal(lot.available_amount) - consumed)
            lot.consumed_amount = money(decimal(lot.consumed_amount) + consumed)
            lot.status = "DEPLETED" if lot.available_amount == ZERO else "ACTIVE"
            lot.row_version = int(lot.row_version or 0) + 1
            lot.save(ignore_permissions=True)
        append_allocation(
            account=account.name,
            ledger_entry=entry.name,
            allocation_type="REDEEM_LOT_CONSUME",
            bonus_amount=consumed,
            idempotency_key=f"{entry.idempotency_key}:lot:{lot.name}",
            source_doctype=entry.source_doctype,
            source_name=entry.source_name,
            bonus_lot=lot.name,
            sequence=sequence,
        )
        remaining -= consumed
    if remaining > ZERO:
        frappe.throw("Bonus lots не покривають redemption", title="LOYALTY_RECONCILIATION_MISMATCH")


def _reduce_origin_lot(entry, amount, values):
    original_entry = values.get("original_entry") or values.get("reversal_of")
    if not original_entry:
        return
    lot_name = frappe.db.get_value("UA Loyalty Bonus Lot", {"source_ledger_entry": original_entry}, "name")
    if not lot_name:
        return
    lot = frappe.get_doc("UA Loyalty Bonus Lot", lot_name)
    reduced = min(money(amount), money(lot.available_amount))
    if reduced <= ZERO:
        return
    with service_write():
        lot.available_amount = money(decimal(lot.available_amount) - reduced)
        lot.reversed_amount = money(decimal(lot.reversed_amount) + reduced)
        lot.status = "REVERSED" if lot.available_amount == ZERO else lot.status
        lot.row_version = int(lot.row_version or 0) + 1
        lot.save(ignore_permissions=True)


def _create_credit_lot(account, entry, entry_type, active_delta, pending_delta, values):
    credit = active_delta if active_delta > ZERO else pending_delta
    if entry_type not in ENTRY_ACTIVE_CREDITS | ENTRY_PENDING_CREDITS or credit <= ZERO:
        return None
    debt = apply_credit(entry.balance_before, active_delta).debt_offset if active_delta > ZERO else ZERO
    status = "ACTIVE" if active_delta > ZERO else "PENDING"
    available = money(active_delta - debt) if active_delta > ZERO else ZERO
    with service_write():
        lot = frappe.get_doc(
            {
                "doctype": "UA Loyalty Bonus Lot",
                "account": account.name,
                "source_ledger_entry": entry.name,
                "lot_kind": values.get("lot_kind") or ("RETURNED" if "RESTORE" in entry_type else "EARNED"),
                "credited_amount": credit,
                "pending_amount": pending_delta if pending_delta > ZERO else ZERO,
                "activated_amount": active_delta if active_delta > ZERO else ZERO,
                "available_amount": available,
                "debt_offset_amount": debt,
                "effective_datetime": entry.effective_datetime,
                "expires_at": entry.expires_at,
                "status": status if available > ZERO or pending_delta > ZERO else "DEPLETED",
                "issuer_company": values.get("issuer_company") or values.get("company"),
            }
        ).insert(ignore_permissions=True)
    if entry_type not in ENTRY_PENDING_CREDITS and entry.expires_at:
        _create_expiry_obligation(account, entry, lot, values)
    if debt > ZERO:
        append_allocation(
            account=account.name,
            ledger_entry=entry.name,
            allocation_type="DEBT_OFFSET",
            bonus_amount=debt,
            idempotency_key=f"{entry.idempotency_key}:debt-offset",
            source_doctype=entry.source_doctype,
            source_name=entry.source_name,
            bonus_lot=lot.name,
        )
    return lot


def _create_expiry_obligation(account, entry, lot, values):
    idempotency_key = f"expiry-obligation:{entry.idempotency_key}"
    existing = frappe.db.get_value("UA Loyalty Expiry Obligation", {"idempotency_key": idempotency_key}, "name")
    if existing:
        return frappe.get_doc("UA Loyalty Expiry Obligation", existing)
    with service_write():
        return frappe.get_doc(
            {
                "doctype": "UA Loyalty Expiry Obligation",
                "account": account.name,
                "scope": account.scope,
                "program": account.program,
                "program_version": values.get("program_version"),
                "source_ledger_entry": entry.name,
                "source_bonus_lot": lot.name,
                "nominal_amount": lot.available_amount,
                "due_datetime": entry.expires_at,
                "status": "SCHEDULED",
                "writeoff_mode": values.get("expiry_writeoff_mode") or "AGGREGATE_NOMINAL_CAP",
                "idempotency_key": idempotency_key,
            }
        ).insert(ignore_permissions=True)


def append_metric(
    account,
    *,
    entry_type: str,
    delta: Decimal,
    idempotency_key: str,
    source_doctype: str,
    source_name: str,
    values: dict[str, Any] | None = None,
):
    values = values or {}
    payload = {
        "account": account.name,
        "entry_type": entry_type,
        "delta": str(money(delta)),
        "source": source_name,
        "row": values.get("source_row_name"),
    }
    fingerprint = payload_hash(payload)
    existing = frappe.db.get_value(
        "UA Loyalty Metric Entry", {"idempotency_key": idempotency_key}, ["name", "payload_hash"], as_dict=True
    )
    if existing:
        if existing.payload_hash != fingerprint:
            raise LoyaltyConflict("Metric idempotency conflict", "LOYALTY_IDEMPOTENCY_CONFLICT")
        return frappe.get_doc("UA Loyalty Metric Entry", existing.name)
    before = money(account.metric_balance)
    update_metric(account, decimal(delta))
    doc_values = {
        "doctype": "UA Loyalty Metric Entry",
        "account": account.name,
        "scope": account.scope,
        "program_version": values.get("program_version"),
        "entry_type": entry_type,
        "posting_datetime": values.get("posting_datetime") or frappe.utils.now_datetime(),
        "metric_delta": money(delta),
        "source_doctype": source_doctype,
        "source_name": source_name,
        "source_row_name": values.get("source_row_name"),
        "company": values.get("company"),
        "fop_profile": values.get("fop_profile"),
        "pos_cash_desk": values.get("pos_cash_desk"),
        "original_entry": values.get("original_entry"),
        "reversal_of": values.get("reversal_of"),
        "idempotency_key": idempotency_key,
        "payload_hash": fingerprint,
        "metric_before": before,
        "metric_after": money(account.metric_balance),
        "metadata_json": values.get("metadata_json"),
    }
    with service_write():
        return frappe.get_doc(doc_values).insert(ignore_permissions=True)


def append_allocation(
    *,
    account: str,
    ledger_entry: str,
    allocation_type: str,
    bonus_amount: Decimal,
    idempotency_key: str,
    source_doctype: str,
    source_name: str,
    **values,
):
    existing = frappe.db.get_value("UA Loyalty Allocation", {"idempotency_key": idempotency_key}, "name")
    if existing:
        return frappe.get_doc("UA Loyalty Allocation", existing)
    doc_values = {
        "doctype": "UA Loyalty Allocation",
        "allocation_type": allocation_type,
        "account": account,
        "ledger_entry": ledger_entry,
        "bonus_amount": money(bonus_amount),
        "idempotency_key": idempotency_key,
        "source_doctype": source_doctype,
        "source_name": source_name,
    }
    allowed = {
        "bonus_lot",
        "source_row_name",
        "original_allocation",
        "return_doctype",
        "return_name",
        "return_row_name",
        "item_code",
        "batch_no",
        "serial_no",
        "source_qty",
        "affected_qty",
        "source_item_amount",
        "sequence",
        "metadata_json",
    }
    doc_values.update({key: value for key, value in values.items() if key in allowed and value is not None})
    with service_write():
        return frappe.get_doc(doc_values).insert(ignore_permissions=True)
