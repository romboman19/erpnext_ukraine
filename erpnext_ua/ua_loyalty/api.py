from __future__ import annotations

from decimal import Decimal

import frappe

from erpnext_ua.ua_loyalty.context import service_write
from erpnext_ua.ua_loyalty.domain.money import decimal, money
from erpnext_ua.ua_loyalty.domain.returns import calculate_return_share
from erpnext_ua.ua_loyalty.exceptions import LoyaltyError
from erpnext_ua.ua_loyalty.services.account_service import account_for, current_tier
from erpnext_ua.ua_loyalty.services.import_service import run_import as run_import_service
from erpnext_ua.ua_loyalty.services.quote_service import quote_order, resolve_location
from erpnext_ua.ua_loyalty.services.reconciliation_service import reconcile_account
from erpnext_ua.ua_loyalty.services.reservation_service import mark_payment_in_progress as mark_payment_service
from erpnext_ua.ua_loyalty.services.reservation_service import release as release_service
from erpnext_ua.ua_loyalty.services.reservation_service import reserve as reserve_service
from erpnext_ua.ua_loyalty.services.settings import settings
from erpnext_ua.ua_loyalty.services.snapshot_service import publish


def _raise(error: LoyaltyError):
    frappe.throw(str(error), title=error.code)


@frappe.whitelist(methods=["POST"])
def identify(pos_session_token: str, identifier: str, identifier_type: str = "AUTO") -> dict:
    from erpnext_ua.ua_pos.api import get_session

    session = get_session(pos_session_token)
    location = resolve_location(cash_desk=session["cash_desk"])
    identifier = (identifier or "").strip()
    try:
        customer, card = _resolve_customer(identifier, identifier_type)
        account = account_for(customer, location.scope)
    except LoyaltyError as error:
        _raise(error)
    tier = current_tier(account)
    return {
        "customer": customer,
        "customer_name": frappe.db.get_value("Customer", customer, "customer_name"),
        "account": account.name,
        "card": card,
        "card_mask": _mask_card(card),
        "scope": account.scope,
        "program": account.program,
        "program_version": frappe.db.get_value("UA Loyalty Program", account.program, "rule_version"),
        "status": account.status,
        "metric_balance": str(money(account.metric_balance)),
        "tier_code": tier.code,
        "earn_percent": str(tier.rate),
        "marketing_balance": str(money(account.marketing_balance)),
        "pending_balance": str(money(account.pending_balance)),
        "reserved_balance": str(money(account.reserved_balance)),
        "redeemable_balance": str(money(account.redeemable_balance)),
        "debt_balance": str(money(account.debt_balance)),
        "row_version": int(account.row_version or 0),
        "warnings": [],
    }


def _resolve_customer(identifier: str, identifier_type: str) -> tuple[str, str | None]:
    identifier_type = (identifier_type or "AUTO").upper()
    if identifier_type in {"AUTO", "CARD"}:
        card = frappe.db.get_value(
            "UA Loyalty Card", {"barcode": identifier.upper()}, ["name", "account", "status"], as_dict=True
        )
        if card:
            if card.status != "ACTIVE":
                raise LoyaltyError("Картку заблоковано", "LOYALTY_CARD_BLOCKED")
            return frappe.db.get_value("UA Loyalty Account", card.account, "customer"), card.name
    if identifier_type in {"AUTO", "CUSTOMER"} and frappe.db.exists("Customer", identifier):
        return identifier, None
    if identifier_type in {"AUTO", "PHONE"}:
        rows = frappe.get_all("Customer", filters={"mobile_no": identifier}, pluck="name", limit=2)
        if len(rows) == 1:
            return rows[0], None
        if len(rows) > 1:
            raise LoyaltyError("Номер телефону належить кільком клієнтам", "LOYALTY_CUSTOMER_AMBIGUOUS")
    raise LoyaltyError("Клієнта або картку не знайдено", "LOYALTY_ACCOUNT_NOT_FOUND")


def _mask_card(card: str | None) -> str | None:
    if not card:
        return None
    barcode = frappe.db.get_value("UA Loyalty Card", card, "barcode") or ""
    return f"****{barcode[-4:]}" if barcode else None


@frappe.whitelist(methods=["POST"])
def enroll(pos_session_token: str, customer: str, barcode: str | None = None) -> dict:
    from erpnext_ua.ua_pos.api import get_session

    session = get_session(pos_session_token)
    config = settings()
    if not config.allow_cashier_account_creation and "UA Loyalty Manager" not in frappe.get_roles():
        frappe.throw("Створення рахунку потребує менеджера", frappe.PermissionError)
    location = resolve_location(cash_desk=session["cash_desk"])
    try:
        account = account_for(customer, location.scope, create=True)
    except LoyaltyError as error:
        _raise(error)
    card = None
    if barcode:
        card = frappe.get_doc(
            {
                "doctype": "UA Loyalty Card",
                "account": account.name,
                "barcode": barcode,
                "status": "ACTIVE",
                "is_primary": 1,
            }
        ).insert(ignore_permissions=True)
    return {"account": account.name, "card": card.name if card else None}


@frappe.whitelist(methods=["POST"])
def quote(pos_session_token: str, source_name: str, requested_redemption: str = "0") -> dict:
    from erpnext_ua.ua_pos.api import _owned_order, get_session

    order = _owned_order(get_session(pos_session_token), source_name, {"Building", "Awaiting Payment"})
    try:
        return quote_order(order, Decimal(requested_redemption or "0"))
    except LoyaltyError as error:
        _raise(error)


@frappe.whitelist(methods=["POST"])
def reserve(pos_session_token: str, source_name: str, quote_hash: str, idempotency_key: str) -> dict:
    from erpnext_ua.ua_pos.api import _owned_order, get_session

    order = _owned_order(get_session(pos_session_token), source_name, {"Building", "Awaiting Payment"})
    try:
        reservation = reserve_service(order, quote_hash=quote_hash, idempotency_key=idempotency_key)
    except LoyaltyError as error:
        _raise(error)
    return reservation.as_dict() if reservation else {"status": "NOT_REQUIRED", "reserved_amount": "0.00"}


@frappe.whitelist(methods=["POST"])
def mark_payment_in_progress(pos_session_token: str, source_name: str) -> dict:
    from erpnext_ua.ua_pos.api import _owned_order, get_session

    order = _owned_order(get_session(pos_session_token), source_name)
    try:
        reservation = mark_payment_service(order)
    except LoyaltyError as error:
        _raise(error)
    return reservation.as_dict() if reservation else {"status": "NOT_REQUIRED"}


@frappe.whitelist(methods=["POST"])
def release(pos_session_token: str, reservation: str, reason_code: str, idempotency_key: str) -> dict:
    from erpnext_ua.ua_pos.api import _owned_order, get_session

    session = get_session(pos_session_token)
    reservation_doc = frappe.get_doc("UA Loyalty Reservation", reservation)
    if reservation_doc.source_doctype != "POS Order":
        frappe.throw("Reservation не належить POS-чеку", frappe.PermissionError)
    _owned_order(session, reservation_doc.source_name)
    try:
        return release_service(
            reservation,
            reason_code=reason_code,
            idempotency_key=idempotency_key,
        ).as_dict()
    except LoyaltyError as error:
        _raise(error)


@frappe.whitelist(methods=["GET"])
def account_summary(account: str) -> dict:
    doc = frappe.get_doc("UA Loyalty Account", account)
    doc.check_permission("read")
    tier = current_tier(doc)
    return {
        "account": doc.name,
        "customer": doc.customer,
        "scope": doc.scope,
        "program": doc.program,
        "tier_code": tier.code,
        "earn_percent": str(tier.rate),
        "metric_balance": str(money(doc.metric_balance)),
        "marketing_balance": str(money(doc.marketing_balance)),
        "pending_balance": str(money(doc.pending_balance)),
        "reserved_balance": str(money(doc.reserved_balance)),
        "redeemable_balance": str(money(doc.redeemable_balance)),
        "debt_balance": str(money(doc.debt_balance)),
    }


@frappe.whitelist(methods=["GET"])
def statement(account: str, limit: int = 100, cursor: str | None = None) -> dict:
    frappe.get_doc("UA Loyalty Account", account).check_permission("read")
    frappe.has_permission("UA Loyalty Ledger Entry", "read", throw=True)
    filters = {"account": account}
    if cursor:
        filters["name"] = ("<", cursor)
    rows = frappe.get_list(
        "UA Loyalty Ledger Entry",
        filters=filters,
        fields=[
            "name",
            "posting_datetime",
            "entry_type",
            "active_delta",
            "pending_delta",
            "balance_after",
            "source_doctype",
            "source_name",
            "reason_code",
        ],
        order_by="posting_datetime desc, name desc",
        limit=min(int(limit), 200),
    )
    return {"rows": rows, "next_cursor": rows[-1].name if len(rows) == min(int(limit), 200) else None}


@frappe.whitelist(methods=["POST"])
def preview_return(pos_session_token: str, original_pos_order: str, items) -> dict:
    from erpnext_ua.ua_loyalty.services.posting_service import _submitted_return_totals
    from erpnext_ua.ua_pos.api import _owned_order, get_session

    original = _owned_order(get_session(pos_session_token), original_pos_order)
    if original.order_type != "Sale" or not original.sales_invoice:
        frappe.throw("Потрібен проведений первинний POS-чек", title="LOYALTY_RETURN_LINK_REQUIRED")
    invoice = frappe.get_doc("Sales Invoice", original.sales_invoice)
    invoice_rows = {row.ua_pos_order_item: row for row in invoice.items}
    order_rows = {row.name: row for row in original.items}
    requested = frappe.parse_json(items) if isinstance(items, str) else items
    earned_reversal = decimal(0)
    redeemed_restore = decimal(0)
    money_refund = decimal(0)
    result_rows = []
    for requested_row in requested or []:
        row_name = requested_row.get("row_name") or requested_row.get("source_row")
        qty = abs(decimal(requested_row.get("qty")))
        order_row = order_rows.get(row_name)
        invoice_row = invoice_rows.get(row_name)
        if not order_row or not invoice_row or qty <= 0 or qty > decimal(order_row.qty):
            frappe.throw("Некоректний рядок повернення", title="LOYALTY_RETURN_LINK_REQUIRED")
        row_earn = decimal(0)
        row_restore = decimal(0)
        allocations = frappe.get_all(
            "UA Loyalty Allocation",
            filters={
                "source_name": invoice.name,
                "source_row_name": invoice_row.name,
                "allocation_type": ("in", ("EARN_ITEM", "REDEEM_ITEM")),
            },
            fields=["name", "allocation_type", "bonus_amount", "source_qty"],
        )
        for allocation in allocations:
            previous_qty, previous_amount = _submitted_return_totals(allocation.name)
            amount = calculate_return_share(
                original_amount=decimal(allocation.bonus_amount),
                original_qty=decimal(allocation.source_qty),
                return_qty=qty,
                previous_return_qty=previous_qty,
                previous_amount=previous_amount,
            )
            if allocation.allocation_type == "EARN_ITEM":
                row_earn += amount
            else:
                row_restore += amount
        row_refund = money(decimal(order_row.amount) * qty / decimal(order_row.qty))
        earned_reversal += row_earn
        redeemed_restore += row_restore
        money_refund += row_refund
        result_rows.append(
            {
                "source_row": row_name,
                "qty": str(qty),
                "money_refund": str(row_refund),
                "earned_reversal": str(money(row_earn)),
                "redeemed_restore": str(money(row_restore)),
            }
        )
    balance = decimal(frappe.db.get_value("UA Loyalty Account", invoice.ua_loyalty_account, "marketing_balance"))
    projected = money(balance - earned_reversal + redeemed_restore)
    return {
        "account": invoice.ua_loyalty_account,
        "money_refund": str(money(money_refund)),
        "earned_reversal": str(money(earned_reversal)),
        "redeemed_restore": str(money(redeemed_restore)),
        "projected_balance": str(projected),
        "projected_debt": str(max(decimal(0), -projected)),
        "items": result_rows,
    }


@frappe.whitelist(methods=["POST"])
def reconcile(account: str, repair: int = 0) -> dict:
    frappe.get_doc("UA Loyalty Account", account).check_permission("read")
    if int(repair) and "UA Loyalty Administrator" not in frappe.get_roles():
        frappe.throw("Repair потребує UA Loyalty Administrator", frappe.PermissionError)
    return reconcile_account(account, repair=bool(int(repair)))


@frappe.whitelist(methods=["POST"])
def publish_program(program: str) -> dict:
    if "UA Loyalty Administrator" not in frappe.get_roles() and "System Manager" not in frappe.get_roles():
        frappe.throw("Publish потребує адміністратора", frappe.PermissionError)
    return publish(program).as_dict()


@frappe.whitelist(methods=["POST"])
def run_import(batch: str) -> dict:
    if "UA Loyalty Administrator" not in frappe.get_roles() and "System Manager" not in frappe.get_roles():
        frappe.throw("Import потребує адміністратора", frappe.PermissionError)
    return run_import_service(batch)


@frappe.whitelist(methods=["POST"])
def request_adjustment(adjustment: str) -> dict:
    doc = frappe.get_doc("UA Loyalty Adjustment", adjustment)
    doc.check_permission("write")
    if doc.docstatus or doc.status != "DRAFT":
        frappe.throw("Коригування вже передано або проведено")
    doc.requested_by = frappe.session.user
    doc.status = "PENDING_APPROVAL"
    doc.save()
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
def approve_adjustment(adjustment: str) -> dict:
    _require_manager()
    doc = frappe.get_doc("UA Loyalty Adjustment", adjustment)
    if doc.docstatus or doc.status != "PENDING_APPROVAL":
        frappe.throw("Коригування не очікує погодження")
    if doc.requested_by == frappe.session.user and money(doc.amount) >= money(settings().dual_control_threshold or 0):
        frappe.throw("Requester і approver мають бути різними для цієї суми")
    doc.approved_by = frappe.session.user
    doc.status = "APPROVED"
    doc.save(ignore_permissions=True)
    doc.submit()
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
def reject_adjustment(adjustment: str, reason: str) -> dict:
    _require_manager()
    doc = frappe.get_doc("UA Loyalty Adjustment", adjustment)
    if doc.docstatus or doc.status != "PENDING_APPROVAL":
        frappe.throw("Коригування не очікує погодження")
    doc.status = "REJECTED"
    doc.comment = f"{doc.comment}\nВідхилено: {reason}".strip()
    doc.approved_by = frappe.session.user
    doc.save(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
def card_action(
    card: str,
    action: str,
    reason: str,
    replacement_barcode: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    _require_manager()
    if not reason or not idempotency_key:
        frappe.throw("Reason та idempotency_key обов’язкові")
    existing = frappe.db.get_value("UA Loyalty Account Change Log", {"idempotency_key": idempotency_key}, "name")
    if existing:
        log = frappe.get_doc("UA Loyalty Account Change Log", existing)
        return {"card": log.card, "status": log.new_value, "change_log": log.name, "existing": True}
    doc = frappe.get_doc("UA Loyalty Card", card)
    old_status = doc.status
    action = action.upper()
    target = {
        "BLOCK": "BLOCKED",
        "UNBLOCK": "ACTIVE",
        "MARK_LOST": "LOST",
        "CLOSE": "CLOSED",
        "REPLACE": "REPLACED",
    }.get(action)
    if not target:
        frappe.throw("Невідома дія з карткою")
    replacement = None
    was_primary = bool(doc.is_primary)
    doc.status = target
    doc.is_primary = 0 if target != "ACTIVE" else doc.is_primary
    if target in {"CLOSED", "REPLACED"}:
        doc.closed_at = frappe.utils.now_datetime()
    if target == "BLOCKED":
        doc.block_reason = reason
    if target == "CLOSED":
        doc.close_reason = reason
    doc.save(ignore_permissions=True)
    if action == "REPLACE":
        if not replacement_barcode:
            frappe.throw("Для заміни потрібен новий barcode")
        replacement = frappe.get_doc(
            {
                "doctype": "UA Loyalty Card",
                "account": doc.account,
                "barcode": replacement_barcode,
                "status": "ACTIVE",
                "is_primary": was_primary,
                "replaces_card": doc.name,
            }
        ).insert(ignore_permissions=True)
        doc.replaced_by = replacement.name
        doc.save(ignore_permissions=True)
    log = _audit_change(
        account=doc.account,
        card=doc.name,
        change_type=f"CARD_{action}",
        old_value=old_status,
        new_value=replacement.name if replacement else target,
        reason=reason,
        idempotency_key=idempotency_key,
        metadata={"replacement": replacement.name if replacement else None},
    )
    return {
        "card": doc.name,
        "status": target,
        "replacement": replacement.name if replacement else None,
        "change_log": log.name,
        "existing": False,
    }


def _audit_change(**values):
    metadata = values.pop("metadata", {})
    with service_write():
        return frappe.get_doc(
            {
                "doctype": "UA Loyalty Account Change Log",
                **values,
                "changed_by": frappe.session.user,
                "changed_at": frappe.utils.now_datetime(),
                "source_doctype": "UA Loyalty Card",
                "source_name": values.get("card"),
                "metadata_json": frappe.as_json(metadata),
            }
        ).insert(ignore_permissions=True)


def _require_manager():
    roles = set(frappe.get_roles())
    if not roles.intersection({"System Manager", "UA Loyalty Manager", "UA Loyalty Administrator"}):
        frappe.throw("Операція потребує менеджера лояльності", frappe.PermissionError)
