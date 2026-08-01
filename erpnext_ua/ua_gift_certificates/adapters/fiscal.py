from __future__ import annotations

import json

import frappe

from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money


def fiscalize_certificate_sale(order, desk, sale):
    if not desk.prro_cash_register:
        return None
    from erpnext_ua.ua_fiscal import orchestration
    from erpnext_ua.ua_fiscal.payment import fiscal_payform_name

    register = frappe.get_doc("PRRO Cash Register", desk.prro_cash_register)
    key = desk.default_kep_key or register.default_kep_key
    if not register.current_shift:
        orchestration.open_shift(register.name, key)
    first = frappe.get_doc("UA Gift Certificate", sale.certificates[0].certificate)
    program = frappe.get_doc("UA Gift Certificate Program", first.program)
    compliance = frappe.get_doc("UA Gift Certificate Compliance Profile", program.compliance_profile)
    mapping = frappe.parse_json(compliance.prro_sale_mapping or "{}") or {}
    if not mapping.get("item_name"):
        frappe.throw("Certificate sale PRRO mapping is not configured", title="CERT_COMPLIANCE_DENIED")
    items = [
        {
            "code": row.public_serial,
            "name": mapping["item_name"],
            "uom": mapping.get("uom") or "шт",
            "unit_cd": mapping.get("unit_code"),
            "letters": mapping.get("tax_letters"),
            "qty": 1,
            "price": row.sale_price,
            "amount": row.sale_price,
        }
        for row in sale.certificates
    ]
    payments = []
    for payment in order.payments_plan:
        if payment.status != "Confirmed":
            continue
        code = int(payment.prro_payment_code)
        payments.append(
            {
                "code": code,
                "name": fiscal_payform_name(payment.kind, code, payment.prro_payment_means),
                "form": payment.prro_payment_form,
                "sum": payment.amount,
                **(
                    {"provided": payment.tendered_amount, "remains": payment.change_amount}
                    if payment.kind == "Cash"
                    else {}
                ),
            }
        )
    receipt = orchestration.fiscalize_sale(
        cash_register=register.name,
        kep_key=key,
        items=items,
        payments=payments,
        total=sale.sale_price_total,
        taxes=[],
        pos_order=order.name,
        idem_key=f"gift-certificate-sale:{register.name}:{sale.name}",
    )
    frappe.db.set_value("PRRO Receipt", receipt, "gift_certificate_sale", sale.name, update_modified=False)
    return receipt


def attach_receipt_snapshot(receipt_name: str | None, order, fiscal_rows: list[dict]):
    if not receipt_name or not order.get("gift_certificate_redeemed_total"):
        return
    certificate_rows = [row for row in fiscal_rows if row.get("name") == "ПОДАРУНКОВИЙ СЕРТИФІКАТ"]
    total = money(sum((money(row.get("sum")) for row in certificate_rows), ZERO))
    profile_versions = []
    for row in order.payments_plan:
        if row.kind == "Gift Certificate" and row.gift_certificate:
            profile_versions.append(
                frappe.db.get_value("UA Gift Certificate", row.gift_certificate, "compliance_profile_version")
            )
    frappe.db.set_value(
        "PRRO Receipt",
        receipt_name,
        {
            "gift_certificate_sale": order.get("gift_certificate_sale"),
            "gift_certificate_payment_total": total,
            "gift_certificate_payment_rows_json": json.dumps(
                certificate_rows, ensure_ascii=False, sort_keys=True, default=str
            ),
            "gift_certificate_tax_event_status": "Not Applicable",
            "gift_certificate_compliance_profile_version": ",".join(filter(None, profile_versions)),
        },
        update_modified=False,
    )
