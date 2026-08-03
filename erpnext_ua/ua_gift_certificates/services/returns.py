from __future__ import annotations

from collections import defaultdict

import frappe

from erpnext_ua.ua_gift_certificates.context import service_write
from erpnext_ua.ua_gift_certificates.domain.allocation import restore_share
from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError
from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money

from .ledger import append_entry


def restore_return_invoice(return_invoice, return_order):
    if not return_invoice.is_return or not return_invoice.return_against:
        return []
    original_invoice = frappe.get_doc("Sales Invoice", return_invoice.return_against)
    if not original_invoice.get("ua_gift_certificate_context"):
        return []
    original_order = frappe.get_doc("POS Order", return_order.return_against)
    results = []
    original_pos_by_return_row = {row.name: row.return_against_item for row in return_order.items}
    return_invoice_by_pos = defaultdict(list)
    for row in return_invoice.items:
        if row.get("ua_pos_order_item"):
            return_invoice_by_pos[row.ua_pos_order_item].append(row)
    for return_pos_row in return_order.items:
        original_pos_row = original_pos_by_return_row[return_pos_row.name]
        allocations = frappe.get_all(
            "UA Gift Certificate Redemption Allocation",
            filters={
                "pos_order": original_order.name,
                "pos_order_item": original_pos_row,
                "sales_invoice": original_invoice.name,
            },
            fields=["*"],
            order_by="allocation_sequence, creation",
        )
        invoice_rows = return_invoice_by_pos.get(return_pos_row.name, [])
        route_return_qty = sum((abs(row.qty) for row in invoice_rows), 0)
        for allocation in allocations:
            already = money(
                frappe.db.sql(
                    """select coalesce(sum(certificate_amount_to_restore), 0)
                       from `tabUA Gift Certificate Return Allocation`
                       where original_redemption_allocation=%s""",
                    allocation.name,
                )[0][0]
            )
            already_qty = frappe.db.sql(
                """select coalesce(sum(qty_returned), 0)
                   from `tabUA Gift Certificate Return Allocation`
                   where original_redemption_allocation=%s""",
                allocation.name,
            )[0][0]
            restore = restore_share(
                allocation.certificate_amount,
                route_return_qty,
                allocation.qty,
                already,
                already_qty,
            )
            if restore <= ZERO:
                continue
            remaining = money(allocation.certificate_amount) - already
            paid_available = money(allocation.paid_component_amount) - money(
                frappe.db.sql(
                    """select coalesce(sum(paid_amount_to_restore), 0)
                       from `tabUA Gift Certificate Return Allocation`
                       where original_redemption_allocation=%s""",
                    allocation.name,
                )[0][0]
            )
            paid = min(
                paid_available,
                money(restore * money(allocation.paid_component_amount) / money(allocation.certificate_amount)),
            )
            if restore == remaining:
                paid = paid_available
            promotional = money(restore - paid)
            target = resolve_restore_target(allocation.certificate, return_order.name)
            paid_entry = (
                append_entry(
                    target,
                    transaction_type="Restore Paid",
                    paid_delta=paid,
                    idempotency_key=f"restore:{return_invoice.name}:{allocation.name}:paid",
                    reference_doctype="Sales Invoice",
                    reference_name=return_invoice.name,
                    reason_code="GOODS_RETURN",
                    values={
                        "sales_invoice": return_invoice.name,
                        "pos_order": return_order.name,
                        "issuer_company": allocation.issuer_company,
                        "redeemer_company": allocation.redeemer_company,
                        "liability_delta": paid,
                    },
                )
                if paid
                else None
            )
            target.reload()
            promotional_entry = (
                append_entry(
                    target,
                    transaction_type="Restore Promotional",
                    promotional_delta=promotional,
                    idempotency_key=f"restore:{return_invoice.name}:{allocation.name}:promotional",
                    reference_doctype="Sales Invoice",
                    reference_name=return_invoice.name,
                    reason_code="GOODS_RETURN",
                    values={"sales_invoice": return_invoice.name, "pos_order": return_order.name},
                )
                if promotional
                else None
            )
            settlement_reversal = _reverse_settlement(allocation, return_invoice.name, paid, promotional)
            invoice_row = invoice_rows[0] if invoice_rows else None
            with service_write():
                result = frappe.get_doc(
                    {
                        "doctype": "UA Gift Certificate Return Allocation",
                        "return_pos_order": return_order.name,
                        "return_pos_order_item": return_pos_row.name,
                        "return_sales_invoice": return_invoice.name,
                        "return_sales_invoice_item": invoice_row.name if invoice_row else return_pos_row.name,
                        "original_redemption_allocation": allocation.name,
                        "qty_returned": route_return_qty,
                        "certificate_amount_to_restore": restore,
                        "paid_amount_to_restore": paid,
                        "promotional_amount_to_restore": promotional,
                        "target_certificate": target.name,
                        "restore_mode": "Same Certificate"
                        if target.name == allocation.certificate
                        else "Replacement Certificate",
                        "paid_ledger_entry": paid_entry.name if paid_entry else None,
                        "promotional_ledger_entry": promotional_entry.name if promotional_entry else None,
                        "settlement_reversal": settlement_reversal.name if settlement_reversal else None,
                        "idempotency_key": f"return-allocation:{return_invoice.name}:{allocation.name}",
                    }
                ).insert(ignore_permissions=True)
            results.append(result)
    return results


def resolve_restore_target(certificate_name: str, return_reference: str):
    certificate = frappe.get_doc("UA Gift Certificate", certificate_name)
    visited = set()
    while certificate.status == "Replaced" and certificate.replaced_by:
        if certificate.name in visited:
            raise GiftCertificateError("Certificate replacement lineage is cyclic", "CERT_MANUAL_REVIEW_REQUIRED")
        visited.add(certificate.name)
        certificate = frappe.get_doc("UA Gift Certificate", certificate.replaced_by)
    program = frappe.get_doc("UA Gift Certificate Program", certificate.program)
    if (
        certificate.status in {"Blocked", "Expired", "Refunded", "Cancelled"}
        or program.usage_policy == "Single Use No Change"
    ):
        from .replacement import replace_certificate

        replacement, _token = replace_certificate(
            certificate.name,
            reason="Return Restore",
            idempotency_key=f"return-target:{return_reference}:{certificate.name}",
            approved_by=frappe.session.user,
            allow_same_approver=True,
        )
        return replacement
    return certificate


def validate_return_cancellation(return_invoice):
    rows = frappe.get_all(
        "UA Gift Certificate Return Allocation",
        filters={"return_sales_invoice": return_invoice.name},
        fields=["target_certificate", "certificate_amount_to_restore", "creation"],
    )
    for row in rows:
        downstream = frappe.db.exists(
            "UA Gift Certificate Ledger Entry",
            {
                "certificate": row.target_certificate,
                "transaction_type": ("in", ["Redeem Paid", "Redeem Promotional"]),
                "creation": (">", row.creation),
            },
        )
        if downstream:
            raise GiftCertificateError(
                "Restored certificate value has already been used; cancel is unsafe",
                "CERT_MANUAL_REVIEW_REQUIRED",
                details={"downstream_ledger_entry": downstream},
            )


def reverse_invoice_certificate_effect(invoice):
    if invoice.is_return:
        validate_return_cancellation(invoice)
        for row in frappe.get_all(
            "UA Gift Certificate Return Allocation", filters={"return_sales_invoice": invoice.name}, fields=["*"]
        ):
            target = frappe.get_doc("UA Gift Certificate", row.target_certificate)
            if money(row.paid_amount_to_restore):
                append_entry(
                    target,
                    transaction_type="Redeem Paid",
                    paid_delta=-money(row.paid_amount_to_restore),
                    idempotency_key=f"cancel-return:{invoice.name}:{row.name}:paid",
                    reference_doctype="Sales Invoice",
                    reference_name=invoice.name,
                    reason_code="CANCEL_RETURN",
                    values={"reversal_of": row.paid_ledger_entry, "sales_invoice": invoice.name},
                )
            target.reload()
            if money(row.promotional_amount_to_restore):
                append_entry(
                    target,
                    transaction_type="Redeem Promotional",
                    promotional_delta=-money(row.promotional_amount_to_restore),
                    idempotency_key=f"cancel-return:{invoice.name}:{row.name}:promotional",
                    reference_doctype="Sales Invoice",
                    reference_name=invoice.name,
                    reason_code="CANCEL_RETURN",
                    values={"reversal_of": row.promotional_ledger_entry, "sales_invoice": invoice.name},
                )
            if row.settlement_reversal:
                _reverse_existing_settlement(
                    row.settlement_reversal,
                    idempotency_key=f"settlement-cancel-return:{invoice.name}:{row.name}",
                )
        return
    allocations = frappe.get_all(
        "UA Gift Certificate Redemption Allocation", filters={"sales_invoice": invoice.name}, fields=["*"]
    )
    for allocation in allocations:
        target = resolve_restore_target(allocation.certificate, f"cancel:{invoice.name}")
        if money(allocation.paid_component_amount):
            append_entry(
                target,
                transaction_type="Restore Paid",
                paid_delta=allocation.paid_component_amount,
                idempotency_key=f"cancel-sale:{invoice.name}:{allocation.name}:paid",
                reference_doctype="Sales Invoice",
                reference_name=invoice.name,
                reason_code="CANCEL_SALE",
                values={"reversal_of": allocation.ledger_entry_paid, "sales_invoice": invoice.name},
            )
        target.reload()
        if money(allocation.promotional_component_amount):
            append_entry(
                target,
                transaction_type="Restore Promotional",
                promotional_delta=allocation.promotional_component_amount,
                idempotency_key=f"cancel-sale:{invoice.name}:{allocation.name}:promotional",
                reference_doctype="Sales Invoice",
                reference_name=invoice.name,
                reason_code="CANCEL_SALE",
                values={"reversal_of": allocation.ledger_entry_promotional, "sales_invoice": invoice.name},
            )
        _reverse_settlement(
            allocation,
            f"cancel:{invoice.name}",
            allocation.paid_component_amount,
            allocation.promotional_component_amount,
        )


def _reverse_settlement(allocation, return_invoice: str, paid, promotional):
    if not allocation.settlement_entry:
        return None
    original = frappe.get_doc("UA Gift Certificate Settlement Entry", allocation.settlement_entry)
    amount = money(paid) + money(promotional)
    with service_write():
        return frappe.get_doc(
            {
                "doctype": "UA Gift Certificate Settlement Entry",
                "network": original.network,
                "certificate": original.certificate,
                "redemption_allocation": allocation.name,
                "issuer_company": original.issuer_company,
                "issuer_fop_profile": original.issuer_fop_profile,
                "redeemer_company": original.redeemer_company,
                "redeemer_fop_profile": original.redeemer_fop_profile,
                "face_amount": -amount,
                "paid_funding_amount": -money(paid),
                "promotional_funding_amount": -money(promotional),
                "amount_due_to_redeemer": -amount,
                "sponsor_entity": original.sponsor_entity,
                "posting_date": frappe.utils.today(),
                "status": "Reversed",
                "reversal_of": original.name,
                "idempotency_key": f"settlement-return:{return_invoice}:{allocation.name}",
            }
        ).insert(ignore_permissions=True)


def _reverse_existing_settlement(settlement_name: str, *, idempotency_key: str):
    original = frappe.get_doc("UA Gift Certificate Settlement Entry", settlement_name)
    existing = frappe.db.get_value(
        "UA Gift Certificate Settlement Entry", {"idempotency_key": idempotency_key}, "name"
    )
    if existing:
        return frappe.get_doc("UA Gift Certificate Settlement Entry", existing)
    with service_write():
        return frappe.get_doc(
            {
                "doctype": "UA Gift Certificate Settlement Entry",
                "network": original.network,
                "certificate": original.certificate,
                "redemption_allocation": original.redemption_allocation,
                "issuer_company": original.issuer_company,
                "issuer_fop_profile": original.issuer_fop_profile,
                "redeemer_company": original.redeemer_company,
                "redeemer_fop_profile": original.redeemer_fop_profile,
                "face_amount": -money(original.face_amount),
                "paid_funding_amount": -money(original.paid_funding_amount),
                "promotional_funding_amount": -money(original.promotional_funding_amount),
                "amount_due_to_redeemer": -money(original.amount_due_to_redeemer),
                "sponsor_entity": original.sponsor_entity,
                "posting_date": frappe.utils.today(),
                "status": "Reversed",
                "reversal_of": original.name,
                "idempotency_key": idempotency_key,
            }
        ).insert(ignore_permissions=True)
