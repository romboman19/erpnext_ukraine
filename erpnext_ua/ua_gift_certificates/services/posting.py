from __future__ import annotations

import json
from decimal import Decimal

import frappe

from erpnext_ua.ua_gift_certificates.context import service_write
from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError
from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money

from .common import lock_certificate
from .ledger import append_entry


def consume_order(invoice, order):
    if invoice.get("ua_sale_fulfillment") and invoice.get("ua_fulfillment_route"):
        return _consume_fulfillment_order(invoice, order)
    reservations = frappe.get_all(
        "UA Gift Certificate Reservation",
        filters={"pos_order": order.name, "status": ("in", ["Active", "Consuming", "Consumed"])},
        fields=["name", "certificate", "status"],
        order_by="certificate, creation",
    )
    if not reservations:
        return []
    results = []
    for row in reservations:
        if row.status == "Consumed":
            results.extend(
                frappe.get_all(
                    "UA Gift Certificate Redemption Allocation", filters={"reservation": row.name}, pluck="name"
                )
            )
            continue
        frappe.db.sql("select name from `tabUA Gift Certificate Reservation` where name=%s for update", row.name)
        reservation = frappe.get_doc("UA Gift Certificate Reservation", row.name)
        certificate = lock_certificate(reservation.certificate)
        payload = json.loads(reservation.policy_snapshot_json)
        paid = money(reservation.paid_component_reserved)
        promotional = money(reservation.promotional_component_reserved)
        paid_entry = append_entry(
            certificate,
            transaction_type="Redeem Paid",
            paid_delta=-paid,
            reserved_delta=-money(reservation.requested_amount),
            idempotency_key=f"consume:{invoice.name}:{certificate.name}:paid",
            reference_doctype="Sales Invoice",
            reference_name=invoice.name,
            reason_code="GOODS_REDEMPTION",
            values={
                "reservation": reservation.name,
                "pos_order": order.name,
                "sales_invoice": invoice.name,
                "issuer_company": certificate.issuer_company,
                "redeemer_company": invoice.company,
                "cash_desk": order.cash_desk,
                "operational_shift": order.operational_shift,
                "employee": order.employee,
                "liability_delta": -paid,
            },
        )
        certificate.reload()
        promotional_entry = append_entry(
            certificate,
            transaction_type="Redeem Promotional",
            promotional_delta=-promotional,
            idempotency_key=f"consume:{invoice.name}:{certificate.name}:promotional",
            reference_doctype="Sales Invoice",
            reference_name=invoice.name,
            reason_code="GOODS_REDEMPTION",
            values={
                "reservation": reservation.name,
                "pos_order": order.name,
                "sales_invoice": invoice.name,
                "issuer_company": certificate.issuer_company,
                "redeemer_company": invoice.company,
            },
        )
        allocations = _create_allocations(
            invoice,
            order,
            reservation,
            certificate,
            payload,
            paid_entry,
            promotional_entry,
        )
        _forfeit_remainder(certificate, invoice, order, payload)
        with service_write():
            reservation.status = "Consumed"
            reservation.consume_idempotency_key = f"consume:{invoice.name}:{certificate.name}"
            reservation.consumed_at = frappe.utils.now_datetime()
            reservation.save(ignore_permissions=True)
        results.extend(allocation.name for allocation in allocations)
    invoice.db_set(
        {
            "ua_gift_certificate_posting_status": "Posted",
            "ua_gift_certificate_redemption_total": money(order.gift_certificate_redeemed_total),
            "ua_gift_certificate_paid_component": money(order.gift_certificate_paid_component),
            "ua_gift_certificate_promotional_component": money(order.gift_certificate_promotional_component),
        },
        update_modified=False,
    )
    order.db_set("gift_certificate_recovery_state", "Locally Completed", update_modified=False)
    return results


def _consume_fulfillment_order(invoice, order):
    from erpnext_ua.ua_gift_certificates.services.fulfillment import invoice_plan

    grouped = {}
    for reservation, part in invoice_plan(order, invoice):
        grouped.setdefault(reservation.name, (reservation, []))[1].append(part)
    results = []
    invoice_total = invoice_paid = invoice_promotional = ZERO
    for reservation_name, (reservation, parts) in grouped.items():
        target = money(sum((part.amount for part in parts), ZERO))
        target_paid = money(sum((part.paid for part in parts), ZERO))
        target_promotional = money(sum((part.promotional for part in parts), ZERO))
        existing = frappe.get_all(
            "UA Gift Certificate Redemption Allocation",
            filters={"reservation": reservation_name, "sales_invoice": invoice.name},
            fields=["name", "certificate_amount", "paid_component_amount", "promotional_component_amount"],
        )
        if existing:
            if (
                money(sum((money(row.certificate_amount) for row in existing), ZERO)) != target
                or money(sum((money(row.paid_component_amount) for row in existing), ZERO)) != target_paid
                or money(sum((money(row.promotional_component_amount) for row in existing), ZERO))
                != target_promotional
            ):
                raise GiftCertificateError(
                    "Gift Certificate fulfillment retry differs from its posted allocation",
                    "CERT_POSTING_INCOMPLETE",
                )
            results.extend(row.name for row in existing)
            invoice_total = money(invoice_total + target)
            invoice_paid = money(invoice_paid + target_paid)
            invoice_promotional = money(invoice_promotional + target_promotional)
            continue

        frappe.db.sql(
            "select name from `tabUA Gift Certificate Reservation` where name=%s for update",
            reservation_name,
        )
        reservation.reload()
        if reservation.status not in {"Active", "Consuming"}:
            raise GiftCertificateError(
                f"Gift Certificate reservation {reservation.name} is {reservation.status}",
                "CERT_POSTING_INCOMPLETE",
            )
        certificate = lock_certificate(reservation.certificate)
        paid_entry, promotional_entry = _fulfillment_ledger_entries(
            invoice,
            order,
            reservation,
            certificate,
            target=target,
            paid=target_paid,
            promotional=target_promotional,
        )
        allocations = _create_fulfillment_allocations(
            invoice,
            order,
            reservation,
            certificate,
            parts,
            paid_entry,
            promotional_entry,
        )
        results.extend(allocation.name for allocation in allocations)
        consumed = money(
            frappe.db.sql(
                """select coalesce(sum(certificate_amount), 0)
                   from `tabUA Gift Certificate Redemption Allocation`
                   where reservation=%s""",
                reservation.name,
            )[0][0]
        )
        requested = money(reservation.requested_amount)
        if consumed > requested:
            raise GiftCertificateError(
                "Gift Certificate fulfillment consumed more than reserved",
                "CERT_POSTING_INCOMPLETE",
            )
        complete = consumed == requested
        if complete:
            _forfeit_remainder(
                certificate,
                invoice,
                order,
                json.loads(reservation.policy_snapshot_json),
            )
        with service_write():
            reservation.status = "Consumed" if complete else "Consuming"
            reservation.consume_idempotency_key = (
                f"consume:{order.name}:{certificate.name}" if complete else None
            )
            reservation.consumed_at = frappe.utils.now_datetime() if complete else None
            reservation.save(ignore_permissions=True)
        invoice_total = money(invoice_total + target)
        invoice_paid = money(invoice_paid + target_paid)
        invoice_promotional = money(invoice_promotional + target_promotional)

    invoice.db_set(
        {
            "ua_gift_certificate_posting_status": "Posted",
            "ua_gift_certificate_redemption_total": invoice_total,
            "ua_gift_certificate_paid_component": invoice_paid,
            "ua_gift_certificate_promotional_component": invoice_promotional,
        },
        update_modified=False,
    )
    pending = frappe.db.exists(
        "UA Gift Certificate Reservation",
        {"pos_order": order.name, "status": ("in", ["Active", "Consuming"])},
    )
    order.db_set(
        "gift_certificate_recovery_state",
        "Consuming" if pending else "Locally Completed",
        update_modified=False,
    )
    return results


def _fulfillment_ledger_entries(
    invoice,
    order,
    reservation,
    certificate,
    *,
    target,
    paid,
    promotional,
):
    common = {
        "reservation": reservation.name,
        "pos_order": order.name,
        "sales_invoice": invoice.name,
        "issuer_company": certificate.issuer_company,
        "redeemer_company": invoice.company,
        "redeemer_fop_profile": invoice.get("ua_fop_profile"),
        "cash_desk": invoice.get("ua_pos_desk") or order.cash_desk,
        "operational_shift": invoice.get("ua_pos_shift") or order.operational_shift,
        "employee": order.employee,
    }
    paid_entry = None
    if paid > ZERO:
        paid_entry = append_entry(
            certificate,
            transaction_type="Redeem Paid",
            paid_delta=-paid,
            reserved_delta=-target,
            idempotency_key=f"consume:{invoice.name}:{certificate.name}:paid",
            reference_doctype="Sales Invoice",
            reference_name=invoice.name,
            reason_code="GOODS_REDEMPTION",
            values={**common, "liability_delta": -paid},
        )
        certificate.reload()
    promotional_entry = None
    if promotional > ZERO:
        promotional_entry = append_entry(
            certificate,
            transaction_type="Redeem Promotional",
            promotional_delta=-promotional,
            reserved_delta=-target if paid <= ZERO else ZERO,
            idempotency_key=f"consume:{invoice.name}:{certificate.name}:promotional",
            reference_doctype="Sales Invoice",
            reference_name=invoice.name,
            reason_code="GOODS_REDEMPTION",
            values=common,
        )
    return paid_entry, promotional_entry


def _create_fulfillment_allocations(
    invoice,
    order,
    reservation,
    certificate,
    parts,
    paid_entry,
    promotional_entry,
):
    order_rows = {row.name: row for row in order.items}
    invoice_rows = {}
    for row in invoice.items:
        if row.get("ua_pos_order_item"):
            invoice_rows.setdefault(row.ua_pos_order_item, row)
    result = []
    for part in parts:
        pos_row = order_rows[part.pos_order_item]
        invoice_row = invoice_rows.get(pos_row.name)
        if not invoice_row:
            raise GiftCertificateError(
                "Sales Invoice fulfillment item allocation is missing",
                "CERT_POSTING_INCOMPLETE",
            )
        item_values = frappe.db.get_value(
            "Item",
            pos_row.item_code,
            ["item_group", "brand"],
            as_dict=True,
        ) or {}
        pos_qty = Decimal(str(pos_row.qty or 0))
        qty_share = part.qty / pos_qty if pos_qty else ZERO
        with service_write():
            document = frappe.get_doc(
                {
                    "doctype": "UA Gift Certificate Redemption Allocation",
                    "certificate": certificate.name,
                    "ledger_entry_paid": paid_entry.name if paid_entry and part.paid else None,
                    "ledger_entry_promotional": (
                        promotional_entry.name
                        if promotional_entry and part.promotional
                        else None
                    ),
                    "reservation": reservation.name,
                    "pos_order": order.name,
                    "pos_order_item": pos_row.name,
                    "sales_invoice": invoice.name,
                    "sales_invoice_item": invoice_row.name,
                    "item_code": pos_row.item_code,
                    "item_group": item_values.get("item_group"),
                    "brand": item_values.get("brand"),
                    "qty": part.qty,
                    "uom": pos_row.uom,
                    "gross_amount": money(money(pos_row.gross_amount) * qty_share),
                    "discount_amount": money(money(pos_row.discount_amount) * qty_share),
                    "net_amount": money(money(pos_row.amount) * qty_share),
                    "certificate_amount": part.amount,
                    "paid_component_amount": part.paid,
                    "promotional_component_amount": part.promotional,
                    "issuer_company": certificate.issuer_company,
                    "issuer_fop_profile": certificate.issuer_fop_profile,
                    "redeemer_company": invoice.company,
                    "redeemer_fop_profile": invoice.get("ua_fop_profile"),
                    "allocation_sequence": part.sequence,
                    "idempotency_key": (
                        f"allocation:{invoice.name}:{reservation.name}:{pos_row.name}"
                    ),
                    "policy_snapshot_json": reservation.policy_snapshot_json,
                }
            ).insert(ignore_permissions=True)
        settlement = _create_settlement(
            document,
            certificate,
            invoice.company,
            invoice.get("ua_fop_profile"),
        )
        if settlement:
            frappe.db.set_value(
                "UA Gift Certificate Redemption Allocation",
                document.name,
                "settlement_entry",
                settlement.name,
                update_modified=False,
            )
        result.append(document)
    return result


def _create_allocations(invoice, order, reservation, certificate, payload, paid_entry, promotional_entry):
    total = money(reservation.requested_amount)
    paid_total = money(reservation.paid_component_reserved)
    paid_allocated = ZERO
    result = []
    rows = payload["allocations"]
    invoice_by_pos_row = {row.ua_pos_order_item: row for row in invoice.items if row.get("ua_pos_order_item")}
    order_by_name = {row.name: row for row in order.items}
    for sequence, allocation in enumerate(rows, 1):
        pos_row = order_by_name[allocation["row"]]
        invoice_row = invoice_by_pos_row.get(pos_row.name)
        if not invoice_row:
            raise GiftCertificateError("Sales Invoice item allocation is missing", "CERT_POSTING_INCOMPLETE")
        amount = money(allocation["amount"])
        paid_amount = (
            money(total and amount * paid_total / total) if sequence < len(rows) else money(paid_total - paid_allocated)
        )
        paid_amount = min(paid_amount, amount)
        promotional_amount = money(amount - paid_amount)
        paid_allocated += paid_amount
        item_values = frappe.db.get_value("Item", pos_row.item_code, ["item_group", "brand"], as_dict=True) or {}
        with service_write():
            document = frappe.get_doc(
                {
                    "doctype": "UA Gift Certificate Redemption Allocation",
                    "certificate": certificate.name,
                    "ledger_entry_paid": paid_entry.name if paid_amount else None,
                    "ledger_entry_promotional": promotional_entry.name if promotional_amount else None,
                    "reservation": reservation.name,
                    "pos_order": order.name,
                    "pos_order_item": pos_row.name,
                    "sales_invoice": invoice.name,
                    "sales_invoice_item": invoice_row.name,
                    "item_code": pos_row.item_code,
                    "item_group": item_values.get("item_group"),
                    "brand": item_values.get("brand"),
                    "qty": pos_row.qty,
                    "uom": pos_row.uom,
                    "gross_amount": money(pos_row.gross_amount),
                    "discount_amount": money(pos_row.discount_amount),
                    "net_amount": money(pos_row.amount),
                    "certificate_amount": amount,
                    "paid_component_amount": paid_amount,
                    "promotional_component_amount": promotional_amount,
                    "issuer_company": certificate.issuer_company,
                    "issuer_fop_profile": certificate.issuer_fop_profile,
                    "redeemer_company": invoice.company,
                    "redeemer_fop_profile": pos_row.fop_profile,
                    "allocation_sequence": sequence,
                    "idempotency_key": f"allocation:{invoice.name}:{certificate.name}:{pos_row.name}",
                    "policy_snapshot_json": reservation.policy_snapshot_json,
                }
            ).insert(ignore_permissions=True)
        settlement = _create_settlement(document, certificate, invoice.company, pos_row.fop_profile)
        if settlement:
            frappe.db.set_value(
                "UA Gift Certificate Redemption Allocation",
                document.name,
                "settlement_entry",
                settlement.name,
                update_modified=False,
            )
        result.append(document)
    return result


def _create_settlement(allocation, certificate, redeemer_company, redeemer_fop):
    if certificate.issuer_company == redeemer_company and (certificate.issuer_fop_profile or None) == (
        redeemer_fop or None
    ):
        return None
    if not frappe.db.get_single_value("UA Gift Certificate Settings", "cross_entity_enabled"):
        raise GiftCertificateError("Cross-entity redemption is disabled", "CERT_SETTLEMENT_PROFILE_MISSING")
    profile = frappe.db.get_value(
        "UA Gift Certificate Settlement Profile",
        {"network": certificate.network, "status": "Active"},
        ["name", "promotional_sponsor"],
        as_dict=True,
    )
    if not profile:
        raise GiftCertificateError("Settlement profile is missing", "CERT_SETTLEMENT_PROFILE_MISSING")
    due = money(allocation.certificate_amount)
    with service_write():
        return frappe.get_doc(
            {
                "doctype": "UA Gift Certificate Settlement Entry",
                "network": certificate.network,
                "certificate": certificate.name,
                "redemption_allocation": allocation.name,
                "issuer_company": certificate.issuer_company,
                "issuer_fop_profile": certificate.issuer_fop_profile,
                "redeemer_company": redeemer_company,
                "redeemer_fop_profile": redeemer_fop,
                "face_amount": allocation.certificate_amount,
                "paid_funding_amount": allocation.paid_component_amount,
                "promotional_funding_amount": allocation.promotional_component_amount,
                "amount_due_to_redeemer": due,
                "sponsor_entity": profile.promotional_sponsor,
                "posting_date": frappe.utils.today(),
                "status": "Open",
                "idempotency_key": f"settlement:{allocation.idempotency_key}",
            }
        ).insert(ignore_permissions=True)


def _forfeit_remainder(certificate, invoice, order, payload):
    forfeited = money(payload.get("forfeited"))
    if forfeited <= ZERO:
        return
    certificate.reload()
    paid = min(money(certificate.paid_balance), forfeited)
    promotional = money(forfeited - paid)
    if paid:
        append_entry(
            certificate,
            transaction_type="Forfeit Paid",
            paid_delta=-paid,
            idempotency_key=f"forfeit:{invoice.name}:{certificate.name}:paid",
            reference_doctype="Sales Invoice",
            reference_name=invoice.name,
            reason_code="SINGLE_USE_REMAINDER",
            values={"pos_order": order.name, "sales_invoice": invoice.name, "liability_delta": -paid},
        )
    if promotional:
        certificate.reload()
        append_entry(
            certificate,
            transaction_type="Forfeit Promotional",
            promotional_delta=-promotional,
            idempotency_key=f"forfeit:{invoice.name}:{certificate.name}:promotional",
            reference_doctype="Sales Invoice",
            reference_name=invoice.name,
            reason_code="SINGLE_USE_REMAINDER",
            values={"pos_order": order.name, "sales_invoice": invoice.name},
        )
