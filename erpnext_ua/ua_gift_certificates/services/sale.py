from __future__ import annotations

import json

import frappe

from erpnext_ua.ua_gift_certificates.context import service_write
from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError
from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money

from .compliance import resolve_profile
from .issuance import activate_certificate, issue_certificate
from .settings import require_enabled


def add_pos_sale_row(
    order,
    *,
    program: str,
    face_value,
    sale_price=None,
    holder_mode=None,
    holder_customer=None,
    idempotency_key: str,
):
    require_enabled(pos_sale=True)
    if order.items or order.get("gift_certificate_redeemed_total"):
        raise GiftCertificateError(
            "Certificate issue and merchandise must use separate POS Orders",
            "CERT_PURCHASE_WITH_CERTIFICATE_DENIED",
        )
    if order.status != "Building":
        raise GiftCertificateError("Order cannot be changed", "CERT_ORDER_CHANGED")
    desk = frappe.get_doc("POS Cash Desk", order.cash_desk)
    fop_profile = frappe.db.get_value("FOP Profile", {"company": desk.company}, "name")
    certificate, _token = issue_certificate(
        program_name=program,
        face_value=face_value,
        sale_price=sale_price,
        holder_mode=holder_mode,
        holder_customer=holder_customer,
        buyer_customer=order.customer,
        issuer_company=desk.company,
        issuer_fop_profile=fop_profile,
        issuer_cash_desk=desk.name,
        pos_order=order.name,
        idempotency_key=f"issue:{idempotency_key}",
    )
    with service_write():
        certificate.status = "Reserved For Sale"
        certificate.save(ignore_permissions=True)
    order.order_purpose = "Gift Certificate Sale"
    order.append(
        "gift_certificate_issue_rows",
        {
            "program": program,
            "certificate": certificate.name,
            "face_value": certificate.face_value,
            "sale_price": certificate.sale_price,
            "holder_mode": certificate.holder_mode,
            "holder_customer": certificate.holder_customer,
            "delivery_mode": "Print",
            "public_serial": certificate.public_serial,
            "masked_token": f"••••{certificate.token_last4}",
            "status": "Reserved",
            "policy_quote_id": idempotency_key,
        },
    )
    order.gift_certificate_face_total = money(order.gift_certificate_face_total) + money(certificate.face_value)
    order.gift_certificate_sale_total = money(order.gift_certificate_sale_total) + money(certificate.sale_price)
    order.net_total = order.gift_certificate_sale_total
    order.grand_total = order.gift_certificate_sale_total
    order.save(ignore_permissions=True)
    return certificate


def prepare_payment(order):
    require_enabled(pos_sale=True)
    if order.order_purpose != "Gift Certificate Sale" or not order.gift_certificate_issue_rows:
        return
    if order.items:
        raise GiftCertificateError("Gift Certificate Sale cannot contain merchandise", "CERT_ORDER_CHANGED")
    desk = frappe.get_doc("POS Cash Desk", order.cash_desk)
    for row in order.gift_certificate_issue_rows:
        certificate = frappe.get_doc("UA Gift Certificate", row.certificate)
        program = frappe.get_doc("UA Gift Certificate Program", certificate.program)
        resolve_profile(
            company=desk.company,
            fop_profile=certificate.issuer_fop_profile,
            profile_name=program.compliance_profile,
            action="sale",
        )
        with service_write():
            certificate.status = "Payment Pending"
            certificate.save(ignore_permissions=True)
    order.gift_certificate_recovery_state = "Payment In Progress"
    order.save(ignore_permissions=True)


def release_pending_sale(order, *, reason: str):
    if order.get("order_purpose") != "Gift Certificate Sale":
        return
    for row in order.gift_certificate_issue_rows:
        certificate = frappe.get_doc("UA Gift Certificate", row.certificate)
        if certificate.status not in {"Reserved For Sale", "Payment Pending"}:
            continue
        with service_write():
            certificate.status = "Issued"
            certificate.save(ignore_permissions=True)
        row.status = "Released"
    order.gift_certificate_recovery_state = "None"
    order.recovery_note = reason
    order.save(ignore_permissions=True)


def complete_pos_sale(order, desk, session):
    existing = frappe.db.get_value("UA Gift Certificate Sale", {"idempotency_key": f"sale:{order.name}"}, "name")
    if existing:
        return frappe.get_doc("UA Gift Certificate Sale", existing)
    if order.status != "Paid" or any(row.status != "Confirmed" for row in order.payments_plan):
        raise GiftCertificateError("Certificate sale payment is not confirmed", "CERT_EXTERNAL_PAYMENT_PENDING")
    paid_total = money(sum((money(row.amount) for row in order.payments_plan), ZERO))
    if paid_total != money(order.gift_certificate_sale_total):
        raise GiftCertificateError("Certificate sale payment total is invalid", "CERT_POSTING_INCOMPLETE")
    sale_rows = []
    certificates = []
    for row in order.gift_certificate_issue_rows:
        certificate = frappe.get_doc("UA Gift Certificate", row.certificate)
        certificates.append(certificate)
        sale_rows.append(
            {
                "certificate": certificate.name,
                "public_serial": certificate.public_serial,
                "masked_token": f"••••{certificate.token_last4}",
                "program": certificate.program,
                "holder_mode": certificate.holder_mode,
                "holder_customer": certificate.holder_customer,
                "face_value": certificate.face_value,
                "sale_price": certificate.sale_price,
                "paid_funding": certificate.initial_paid_funding,
                "promotional_funding": certificate.initial_promotional_funding,
                "premium_fee": certificate.premium_fee,
                "print_required": 1,
                "delivery_channel": row.delivery_mode,
                "status": "Pending",
            }
        )
    with service_write():
        sale = frappe.get_doc(
            {
                "doctype": "UA Gift Certificate Sale",
                "pos_order": order.name,
                "cash_desk": order.cash_desk,
                "operational_shift": order.operational_shift,
                "employee": order.employee,
                "buyer_customer": order.customer,
                "company": desk.company,
                "fop_profile": certificates[0].issuer_fop_profile,
                "posting_date": frappe.utils.today(),
                "posting_time": frappe.utils.nowtime(),
                "status": "Posting",
                "certificates": sale_rows,
                "face_total": money(sum((money(row.face_value) for row in certificates), ZERO)),
                "sale_price_total": money(sum((money(row.sale_price) for row in certificates), ZERO)),
                "paid_funding_total": money(sum((money(row.initial_paid_funding) for row in certificates), ZERO)),
                "promotional_funding_total": money(
                    sum((money(row.initial_promotional_funding) for row in certificates), ZERO)
                ),
                "premium_total": money(sum((money(row.premium_fee) for row in certificates), ZERO)),
                "paid_total": paid_total,
                "idempotency_key": f"sale:{order.name}",
                "policy_snapshot_json": json.dumps(
                    {row.name: json.loads(row.policy_snapshot_json) for row in certificates},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        ).insert(ignore_permissions=True)
    journal = _post_sale_journal(sale, order, certificates)
    sale.journal_entry = journal.name
    sale.accounting_reference_doctype = "Journal Entry"
    sale.accounting_reference_name = journal.name
    sale.status = "Submitted"
    sale.flags.ignore_permissions = True
    sale.submit()
    for certificate in certificates:
        activate_certificate(
            certificate.name,
            sale_reference=sale.name,
            payment_evidence=journal.name,
            idempotency_key=f"activate:{sale.name}:{certificate.name}",
        )
    sale.db_set("status", "Completed", update_modified=False)
    sale.reload()
    order.gift_certificate_sale = sale.name
    order.gift_certificate_recovery_state = "Locally Completed"
    order.status = "Posted"
    order.save(ignore_permissions=True)
    return sale


def _post_sale_journal(sale, order, certificates):
    profile_name = frappe.db.get_value("UA Gift Certificate Program", certificates[0].program, "accounting_profile")
    if any(
        frappe.db.get_value("UA Gift Certificate Program", row.program, "accounting_profile") != profile_name
        for row in certificates
    ):
        raise GiftCertificateError("One sale cannot mix accounting profiles", "CERT_ACCOUNTING_PROFILE_INVALID")
    profile = frappe.get_doc("UA Gift Certificate Accounting Profile", profile_name)
    accounts = []
    for payment in order.payments_plan:
        account = frappe.db.get_value(
            "Mode of Payment Account", {"parent": payment.mode_of_payment, "company": sale.company}, "default_account"
        )
        if not account:
            raise GiftCertificateError("Payment account is not configured", "CERT_ACCOUNTING_PROFILE_INVALID")
        accounts.append(
            {
                "account": account,
                "debit_in_account_currency": money(payment.amount),
                "cost_center": profile.default_cost_center,
            }
        )
    paid = money(sale.paid_funding_total)
    premium = money(sale.premium_total)
    if paid:
        accounts.append(
            {
                "account": profile.paid_liability_account,
                "credit_in_account_currency": paid,
                "cost_center": profile.default_cost_center,
            }
        )
    if premium:
        if not profile.premium_revenue_account:
            raise GiftCertificateError("Premium revenue account is missing", "CERT_ACCOUNTING_PROFILE_INVALID")
        accounts.append(
            {
                "account": profile.premium_revenue_account,
                "credit_in_account_currency": premium,
                "cost_center": profile.default_cost_center,
            }
        )
    journal = frappe.get_doc(
        {
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "company": sale.company,
            "posting_date": sale.posting_date,
            "user_remark": f"Gift Certificate Sale {sale.name}",
            "accounts": accounts,
        }
    ).insert(ignore_permissions=True)
    journal.submit()
    return journal
