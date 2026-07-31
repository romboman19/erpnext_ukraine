from __future__ import annotations

import json
from calendar import monthrange
from datetime import datetime
from decimal import Decimal

import frappe

from erpnext_ua.ua_loyalty.domain.money import ZERO, decimal, money
from erpnext_ua.ua_loyalty.domain.returns import calculate_return_share
from erpnext_ua.ua_loyalty.exceptions import LoyaltyError

from .account_service import lock_account
from .ledger_service import append_allocation, append_ledger, append_metric
from .reservation_service import consume


def post_invoice(invoice):
    if invoice.get("ua_loyalty_posted") or frappe.db.get_value("Sales Invoice", invoice.name, "ua_loyalty_posted"):
        return
    account = lock_account(invoice.ua_loyalty_account)
    if invoice.is_return:
        _post_return(invoice, account)
    else:
        _post_sale(invoice, account)
    _mark_posted(invoice, account)


def _post_sale(invoice, account):
    program = frappe.get_doc("UA Loyalty Program", invoice.ua_loyalty_program)
    redemption = money(invoice.ua_loyalty_redeemed_amount)
    if redemption > ZERO:
        if not invoice.ua_loyalty_reservation:
            raise LoyaltyError("Для списання немає reservation", "LOYALTY_RESERVATION_STATE_CONFLICT")
        consume(invoice.ua_loyalty_reservation, redemption)
        account.reload()
    for row in invoice.items:
        base_values = _entry_values(invoice, row)
        redeemed = money(row.ua_loyalty_redeemed_amount)
        if redeemed > ZERO:
            entry = append_ledger(
                account,
                entry_type="REDEEM",
                active_delta=-redeemed,
                idempotency_key=f"sale:{invoice.name}:{row.name}:redeem",
                source_doctype="Sales Invoice",
                source_name=invoice.name,
                values=base_values,
            )
            append_allocation(
                account=account.name,
                ledger_entry=entry.name,
                allocation_type="REDEEM_ITEM",
                bonus_amount=redeemed,
                idempotency_key=f"{entry.idempotency_key}:allocation",
                source_doctype="Sales Invoice",
                source_name=invoice.name,
                source_row_name=row.name,
                item_code=row.item_code,
                batch_no=row.batch_no,
                serial_no=row.serial_no,
                source_qty=abs(decimal(row.qty)),
                affected_qty=abs(decimal(row.qty)),
                source_item_amount=money(row.amount),
            )
        amount_before = money(row.ua_loyalty_amount_before)
        if row.ua_loyalty_metric_eligible and amount_before > ZERO:
            append_metric(
                account,
                entry_type="SALE_QUALIFYING_AMOUNT",
                delta=amount_before,
                idempotency_key=f"sale:{invoice.name}:{row.name}:metric-sale",
                source_doctype="Sales Invoice",
                source_name=invoice.name,
                values=base_values,
            )
        if redeemed > ZERO:
            append_metric(
                account,
                entry_type="BONUS_REDEEMED",
                delta=-redeemed,
                idempotency_key=f"sale:{invoice.name}:{row.name}:metric-redeem",
                source_doctype="Sales Invoice",
                source_name=invoice.name,
                values=base_values,
            )
        earned = money(row.ua_loyalty_earned_amount)
        if earned <= ZERO:
            continue
        pending = money(invoice.ua_loyalty_earned_pending) > ZERO
        entry_type = "EARN_PENDING" if pending else "EARN_IMMEDIATE"
        entry = append_ledger(
            account,
            entry_type=entry_type,
            active_delta=ZERO if pending else earned,
            pending_delta=earned if pending else ZERO,
            idempotency_key=f"sale:{invoice.name}:{row.name}:earn",
            source_doctype="Sales Invoice",
            source_name=invoice.name,
            values={**base_values, **_credit_schedule(program), "lot_kind": "EARNED"},
        )
        append_allocation(
            account=account.name,
            ledger_entry=entry.name,
            allocation_type="EARN_ITEM",
            bonus_amount=earned,
            idempotency_key=f"{entry.idempotency_key}:allocation",
            source_doctype="Sales Invoice",
            source_name=invoice.name,
            source_row_name=row.name,
            item_code=row.item_code,
            batch_no=row.batch_no,
            serial_no=row.serial_no,
            source_qty=abs(decimal(row.qty)),
            affected_qty=abs(decimal(row.qty)),
            source_item_amount=money(row.ua_loyalty_earn_base),
        )


def _post_return(invoice, account):
    original_rows = {row.name: row for row in frappe.get_doc("Sales Invoice", invoice.return_against).items}
    for row in invoice.items:
        original_row = original_rows.get(row.sales_invoice_item)
        if not original_row:
            raise LoyaltyError("Не знайдено первинний товарний рядок", "LOYALTY_RETURN_LINK_REQUIRED")
        original_allocations = frappe.get_all(
            "UA Loyalty Allocation",
            filters={
                "source_doctype": "Sales Invoice",
                "source_name": invoice.return_against,
                "source_row_name": original_row.name,
                "allocation_type": ("in", ("EARN_ITEM", "REDEEM_ITEM")),
            },
            fields=["name", "allocation_type", "ledger_entry", "bonus_amount", "source_qty"],
        )
        for original in original_allocations:
            previous_qty, previous_amount = _submitted_return_totals(original.name)
            amount = calculate_return_share(
                original_amount=decimal(original.bonus_amount),
                original_qty=decimal(original.source_qty),
                return_qty=abs(decimal(row.qty)),
                previous_return_qty=previous_qty,
                previous_amount=previous_amount,
            )
            if amount <= ZERO:
                continue
            base_values = _entry_values(invoice, row)
            base_values.update({"original_entry": original.ledger_entry, "reversal_of": original.ledger_entry})
            if original.allocation_type == "EARN_ITEM":
                original_type = frappe.db.get_value("UA Loyalty Ledger Entry", original.ledger_entry, "entry_type")
                pending = original_type == "EARN_PENDING"
                entry = append_ledger(
                    account,
                    entry_type="PENDING_REVERSE_RETURN" if pending else "EARN_REVERSE_RETURN",
                    active_delta=ZERO if pending else -amount,
                    pending_delta=-amount if pending else ZERO,
                    idempotency_key=f"return:{invoice.name}:{row.name}:{original.name}:earn-reverse",
                    source_doctype="Sales Invoice",
                    source_name=invoice.name,
                    values=base_values,
                )
                allocation_type = "PENDING_REVERSE" if pending else "RETURN_EARN_REVERSE"
            else:
                program = frappe.get_doc("UA Loyalty Program", invoice.ua_loyalty_program)
                entry = append_ledger(
                    account,
                    entry_type="REDEEM_RETURN_RESTORE",
                    active_delta=amount,
                    idempotency_key=f"return:{invoice.name}:{row.name}:{original.name}:redeem-restore",
                    source_doctype="Sales Invoice",
                    source_name=invoice.name,
                    values={
                        **base_values,
                        **_credit_schedule(program, restored=True),
                        "lot_kind": "RETURNED",
                    },
                )
                allocation_type = "RETURN_REDEEM_RESTORE"
            append_allocation(
                account=account.name,
                ledger_entry=entry.name,
                allocation_type=allocation_type,
                bonus_amount=amount,
                idempotency_key=f"{entry.idempotency_key}:allocation",
                source_doctype="Sales Invoice",
                source_name=invoice.name,
                source_row_name=row.name,
                original_allocation=original.name,
                return_doctype="Sales Invoice",
                return_name=invoice.name,
                return_row_name=row.name,
                item_code=row.item_code,
                batch_no=row.batch_no,
                serial_no=row.serial_no,
                source_qty=decimal(original.source_qty),
                affected_qty=abs(decimal(row.qty)),
                source_item_amount=money(abs(decimal(row.amount))),
            )
        _post_return_metrics(invoice, row, original_row, account)


def _post_return_metrics(invoice, row, original_row, account):
    original_qty = abs(decimal(original_row.qty))
    return_qty = abs(decimal(row.qty))
    prior_qty = _submitted_return_qty(invoice.return_against, original_row.name, exclude=invoice.name)
    prior_qualifying = _submitted_metric_amount(
        invoice.return_against, original_row.name, "RETURN_QUALIFYING_AMOUNT", exclude=invoice.name
    )
    qualifying = calculate_return_share(
        original_amount=money(original_row.ua_loyalty_amount_before)
        if original_row.ua_loyalty_metric_eligible
        else ZERO,
        original_qty=original_qty,
        return_qty=return_qty,
        previous_return_qty=prior_qty,
        previous_amount=prior_qualifying,
    )
    base_values = _entry_values(invoice, row)
    append_metric(
        account,
        entry_type="RETURN_QUALIFYING_AMOUNT",
        delta=-qualifying,
        idempotency_key=f"return:{invoice.name}:{row.name}:metric-return",
        source_doctype="Sales Invoice",
        source_name=invoice.name,
        values=base_values,
    )
    redeemed = money(row.ua_loyalty_redeemed_amount)
    if redeemed > ZERO:
        append_metric(
            account,
            entry_type="BONUS_REDEEM_RESTORED",
            delta=redeemed,
            idempotency_key=f"return:{invoice.name}:{row.name}:metric-restore",
            source_doctype="Sales Invoice",
            source_name=invoice.name,
            values=base_values,
        )


def _submitted_return_totals(original_allocation: str) -> tuple[Decimal, Decimal]:
    rows = frappe.get_all(
        "UA Loyalty Allocation",
        filters={
            "original_allocation": original_allocation,
            "allocation_type": ("in", ("RETURN_EARN_REVERSE", "RETURN_REDEEM_RESTORE", "PENDING_REVERSE")),
        },
        fields=["return_name", "affected_qty", "bonus_amount"],
    )
    valid = {
        row.return_name
        for row in rows
        if row.return_name and frappe.db.get_value("Sales Invoice", row.return_name, "docstatus") == 1
    }
    return (
        sum((decimal(row.affected_qty) for row in rows if row.return_name in valid), ZERO),
        sum((decimal(row.bonus_amount) for row in rows if row.return_name in valid), ZERO),
    )


def _submitted_return_qty(original_invoice: str, original_row: str, *, exclude: str) -> Decimal:
    returns = frappe.get_all(
        "Sales Invoice",
        filters={"return_against": original_invoice, "is_return": 1, "docstatus": 1, "name": ("!=", exclude)},
        pluck="name",
    )
    if not returns:
        return ZERO
    rows = frappe.get_all(
        "Sales Invoice Item", filters={"parent": ("in", returns), "sales_invoice_item": original_row}, fields=["qty"]
    )
    return sum((abs(decimal(row.qty)) for row in rows), ZERO)


def _submitted_metric_amount(original_invoice: str, original_row: str, entry_type: str, *, exclude: str) -> Decimal:
    rows = frappe.get_all(
        "UA Loyalty Metric Entry",
        filters={"entry_type": entry_type, "source_doctype": "Sales Invoice", "source_name": ("!=", exclude)},
        fields=["source_name", "source_row_name", "metric_delta"],
    )
    valid_returns = set(
        frappe.get_all("Sales Invoice", filters={"return_against": original_invoice, "docstatus": 1}, pluck="name")
    )
    return sum(
        (
            abs(decimal(item.metric_delta))
            for item in rows
            if item.source_name in valid_returns
            and frappe.db.get_value("Sales Invoice Item", item.source_row_name, "sales_invoice_item") == original_row
        ),
        ZERO,
    )


def cancel_invoice(invoice):
    account = lock_account(invoice.ua_loyalty_account)
    entries = frappe.get_all(
        "UA Loyalty Ledger Entry",
        filters={"source_doctype": "Sales Invoice", "source_name": invoice.name},
        fields=["name", "entry_type", "active_delta", "pending_delta", "source_row_name"],
    )
    for row in entries:
        append_ledger(
            account,
            entry_type=_inverse_entry_type(row.entry_type),
            active_delta=-decimal(row.active_delta),
            pending_delta=-decimal(row.pending_delta),
            idempotency_key=f"cancel:{invoice.name}:{row.name}",
            source_doctype="Sales Invoice",
            source_name=invoice.name,
            values={
                "source_row_name": row.source_row_name,
                "original_entry": row.name,
                "reversal_of": row.name,
                "sales_invoice": invoice.name,
                "rule_snapshot_hash": invoice.ua_loyalty_snapshot_hash,
            },
        )
    metrics = frappe.get_all(
        "UA Loyalty Metric Entry",
        filters={"source_doctype": "Sales Invoice", "source_name": invoice.name},
        fields=["name", "entry_type", "metric_delta", "source_row_name"],
    )
    for row in metrics:
        append_metric(
            account,
            entry_type=_inverse_metric_type(row.entry_type),
            delta=-decimal(row.metric_delta),
            idempotency_key=f"cancel:{invoice.name}:metric:{row.name}",
            source_doctype="Sales Invoice",
            source_name=invoice.name,
            values={"source_row_name": row.source_row_name, "original_entry": row.name, "reversal_of": row.name},
        )


def _entry_values(invoice, row) -> dict:
    return {
        "program_version": invoice.ua_loyalty_program_version,
        "posting_datetime": frappe.utils.now_datetime(),
        "source_row_name": row.name,
        "pos_order": invoice.ua_pos_order,
        "sales_invoice": invoice.name,
        "company": invoice.company,
        "fop_profile": invoice.get("ua_fop_profile"),
        "pos_cash_desk": invoice.get("ua_pos_desk"),
        "warehouse": row.warehouse,
        "sales_channel": "POS" if invoice.get("ua_pos_order") else "Manual",
        "issuer_company": invoice.company,
        "redeemer_company": invoice.company,
        "rule_snapshot_hash": invoice.ua_loyalty_snapshot_hash,
        "metadata_json": json.dumps(
            {"item_code": row.item_code, "pos_order_item": row.ua_pos_order_item}, ensure_ascii=False, sort_keys=True
        ),
    }


def _credit_schedule(program, *, restored: bool = False) -> dict:
    now = frappe.utils.now_datetime()
    effective = now
    if not restored and program.activation_mode == "AFTER_DAYS":
        effective = frappe.utils.add_days(now, int(program.activation_days or 0))
    elif not restored and program.activation_mode == "NEXT_MONTH_DAY":
        year = now.year + (1 if now.month == 12 else 0)
        month = 1 if now.month == 12 else now.month + 1
        day = min(max(int(program.activation_days or 1), 1), monthrange(year, month)[1])
        effective = datetime.combine(datetime(year, month, day).date(), now.time())

    validity_days = (
        int(program.returned_bonus_validity_days or 0) if restored else int(program.bonus_validity_days or 0)
    )
    expires_at = None
    if program.expiry_mode == "AFTER_DAYS" and validity_days:
        expires_at = frappe.utils.add_days(effective, validity_days)
    elif program.expiry_mode == "END_OF_MONTH":
        last_day = monthrange(effective.year, effective.month)[1]
        expires_at = datetime.combine(datetime(effective.year, effective.month, last_day).date(), datetime.max.time())
    elif program.expiry_mode == "END_OF_YEAR":
        expires_at = datetime.combine(datetime(effective.year, 12, 31).date(), datetime.max.time())
    return {
        "effective_datetime": effective,
        "expires_at": expires_at,
        "expiry_writeoff_mode": program.expiry_writeoff_mode,
    }


def _mark_posted(invoice, account):
    posting_key = (
        invoice.ua_loyalty_posting_key
        or f"{'return' if invoice.is_return else 'sale'}:{invoice.name}:{invoice.ua_loyalty_snapshot_hash}"
    )
    frappe.db.set_value(
        "Sales Invoice",
        invoice.name,
        {"ua_loyalty_posted": 1, "ua_loyalty_posting_key": posting_key},
        update_modified=False,
    )
    if invoice.ua_pos_order:
        frappe.db.set_value(
            "POS Order",
            invoice.ua_pos_order,
            {
                "loyalty_state": "POSTED",
                "loyalty_posting_status": "POSTED",
                "loyalty_posted_at": frappe.utils.now_datetime(),
                "loyalty_balance_after": account.marketing_balance,
                "loyalty_metric_after": account.metric_balance,
                "loyalty_debt_after": account.debt_balance,
            },
            update_modified=False,
        )


def _inverse_entry_type(entry_type: str) -> str:
    if entry_type == "REDEEM":
        return "REDEEM_CANCEL_RESTORE"
    if entry_type in {"EARN_PENDING", "PENDING_REVERSE_RETURN"}:
        return "PENDING_REVERSE_CANCEL"
    if entry_type in {"REDEEM_RETURN_RESTORE", "REDEEM_CANCEL_RESTORE"}:
        return "RETURN_RESTORE_CANCEL_REVERSE"
    return "EARN_REVERSE_CANCEL" if entry_type.startswith("EARN") else "ROUNDING_ADJUSTMENT"


def _inverse_metric_type(entry_type: str) -> str:
    return (
        "BONUS_REDEEM_RESTORED"
        if entry_type == "BONUS_REDEEMED"
        else (
            "BONUS_REDEEMED"
            if entry_type == "BONUS_REDEEM_RESTORED"
            else "RETURN_QUALIFYING_AMOUNT"
            if entry_type == "SALE_QUALIFYING_AMOUNT"
            else "SALE_QUALIFYING_AMOUNT"
        )
    )
