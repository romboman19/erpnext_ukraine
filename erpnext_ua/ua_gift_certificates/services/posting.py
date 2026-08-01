from __future__ import annotations

import json

import frappe

from erpnext_ua.ua_gift_certificates.context import service_write
from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError
from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money

from .common import lock_certificate
from .ledger import append_entry


def consume_order(invoice, order):
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
