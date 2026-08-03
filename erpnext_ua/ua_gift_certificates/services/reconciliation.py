from __future__ import annotations

import frappe

from erpnext_ua.ua_gift_certificates.context import service_write
from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money


def reconcile_certificate(name: str, *, repair_cache: bool = False) -> dict:
    certificate = frappe.get_doc("UA Gift Certificate", name)
    totals = frappe.db.sql(
        """select coalesce(sum(balance_delta),0), coalesce(sum(paid_delta),0),
                  coalesce(sum(promotional_delta),0)
           from `tabUA Gift Certificate Ledger Entry` where certificate=%s""",
        name,
    )[0]
    reserved = money(
        frappe.db.sql(
            """select coalesce(sum(requested_amount),0) from `tabUA Gift Certificate Reservation`
               where certificate=%s and status in ('Active','Consuming')""",
            name,
        )[0][0]
    )
    expected = {
        "current_balance": money(totals[0]),
        "paid_balance": money(totals[1]),
        "promotional_balance": money(totals[2]),
        "reserved_balance": reserved,
        "available_balance": (
            ZERO
            if certificate.status in {"Blocked", "Expired", "Cancelled", "Refunded", "Replaced"}
            else max(money(totals[0]) - reserved, ZERO)
        ),
    }
    mismatches = {
        fieldname: {"cached": str(money(certificate.get(fieldname))), "expected": str(value)}
        for fieldname, value in expected.items()
        if money(certificate.get(fieldname)) != value
    }
    if repair_cache and mismatches:
        with service_write():
            certificate.update(expected)
            certificate.row_version = int(certificate.row_version or 0) + 1
            certificate.save(ignore_permissions=True)
    return {"certificate": name, "status": "Mismatch" if mismatches else "Clean", "mismatches": mismatches}


def run_daily_reconciliation():
    if not frappe.db.table_exists("UA Gift Certificate"):
        return
    for name in frappe.get_all("UA Gift Certificate", pluck="name", limit=1000):
        result = reconcile_certificate(name)
        if result["status"] != "Clean":
            frappe.logger("ua_gift_certificates").error({"event": "reconciliation_mismatch", **result})
