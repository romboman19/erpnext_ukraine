"""Operational partner balances and fail-closed integrity diagnostics."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from ..services.allocation import SOURCE_METHOD_RELATIONSHIP_MODEL
from ..setup.ownership_dimension import (
    OWNERSHIP_CONVERSION_FIELD,
    OWNERSHIP_FIELD,
    PARTNER_RETURN_FIELD,
    SETTLEMENT_REPORT_FIELD,
)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _filters_sql(filters: dict[str, Any] | None, *, alias: str) -> tuple[str, dict[str, Any]]:
    filters = filters or {}
    conditions = []
    values = {}
    for fieldname in ("company", "supplier", "contract"):
        value = filters.get(fieldname)
        if value:
            conditions.append(f"{alias}.`{fieldname}` = %({fieldname})s")
            values[fieldname] = value
    return (" and " + " and ".join(conditions) if conditions else "", values)


def _permitted_companies(frappe: Any) -> set[str]:
    return set(frappe.get_list("Company", pluck="name", limit=0))


def _assert_company_permission(frappe: Any, company: str) -> None:
    if company not in _permitted_companies(frappe):
        frappe.throw(f"Not permitted to read Company {company}", frappe.PermissionError)


def _psbo_024_totals(frappe: Any, company: str) -> tuple[dict[tuple[str, str], tuple[Decimal, Decimal]], ...]:
    """Return operational and simple-ledger 024 totals by UOM and currency."""
    expected: dict[tuple[str, str], list[Decimal]] = defaultdict(
        lambda: [Decimal("0"), Decimal("0")]
    )

    def add(rows: list[Any], sign: int) -> None:
        for row in rows:
            values = expected[(row.uom or "", row.currency or "")]
            values[0] += _decimal(row.quantity) * sign
            values[1] += _decimal(row.amount) * sign

    add(
        frappe.db.sql(
            """
            select lot.stock_uom as uom, lot.off_balance_currency as currency,
                   sum(item.stock_qty) as quantity,
                   sum(item.accounting_amount) as amount
            from `tabCC Receipt Item` item
            inner join `tabCC Receipt` receipt on receipt.name = item.parent
            inner join `tabCC Stock Lot` lot on lot.name = item.stock_lot
            where receipt.company = %s and receipt.docstatus = 1
            group by lot.stock_uom, lot.off_balance_currency
            """,
            (company,),
            as_dict=True,
        ),
        1,
    )
    add(
        frappe.db.sql(
            """
            select lot.stock_uom as uom, lot.off_balance_currency as currency,
                   sum(allocation.sold_qty) as quantity,
                   sum(allocation.off_balance_amount) as amount
            from `tabCC Sale Allocation` allocation
            inner join `tabSales Invoice` invoice
                on invoice.name = allocation.sales_invoice and invoice.docstatus = 1
            inner join `tabCC Stock Lot` lot on lot.name = allocation.stock_lot
            where allocation.company = %s
              and allocation.relationship_model in ('COMMISSION', 'CONSIGNMENT')
            group by lot.stock_uom, lot.off_balance_currency
            """,
            (company,),
            as_dict=True,
        ),
        -1,
    )
    add(
        frappe.db.sql(
            """
            select lot.stock_uom as uom, lot.off_balance_currency as currency,
                   sum(audit.returned_qty) as quantity,
                   sum(audit.off_balance_amount) as amount
            from `tabCC Sale Return Allocation` audit
            inner join `tabSales Invoice` invoice
                on invoice.name = audit.return_sales_invoice and invoice.docstatus = 1
            inner join `tabCC Stock Lot` lot on lot.name = audit.stock_lot
            where audit.company = %s
              and audit.relationship_model in ('COMMISSION', 'CONSIGNMENT')
            group by lot.stock_uom, lot.off_balance_currency
            """,
            (company,),
            as_dict=True,
        ),
        1,
    )
    for doctype in ("CC Partner Return", "CC Ownership Conversion"):
        add(
            frappe.db.sql(
                f"""
                select lot.stock_uom as uom, lot.off_balance_currency as currency,
                       sum(movement.qty) as quantity,
                       sum(movement.off_balance_amount) as amount
                from `tab{doctype}` movement
                inner join `tabCC Stock Lot` lot on lot.name = movement.source_lot
                where movement.company = %s and movement.docstatus = 1
                group by lot.stock_uom, lot.off_balance_currency
                """,
                (company,),
                as_dict=True,
            ),
            -1,
        )

    ledger = {
        (row.uom or "", row.currency or ""): (
            _decimal(row.quantity),
            _decimal(row.amount),
        )
        for row in frappe.db.sql(
            """
            select uom, currency,
                   sum(case when direction = 'Increase' then quantity else -quantity end)
                       as quantity,
                   sum(case when direction = 'Increase' then amount else -amount end)
                       as amount
            from `tabUA Off Balance Entry`
            where company = %s and docstatus = 1
              and reference_doctype in (
                  'CC Receipt', 'Sales Invoice',
                  'CC Partner Return', 'CC Ownership Conversion'
              )
            group by uom, currency
            """,
            (company,),
            as_dict=True,
        )
    }
    normalized = {
        key: (values[0], values[1])
        for key, values in expected.items()
        if values[0] or values[1]
    }
    return normalized, ledger


def get_partner_balances(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Aggregate unreported, payable, paid, outstanding and overdue partner debt."""
    import frappe
    from frappe.utils import getdate, nowdate

    filters = dict(filters or {})
    if filters.get("company"):
        _assert_company_permission(frappe, filters["company"])
    else:
        permitted = sorted(_permitted_companies(frappe))
        return [
            row
            for company in permitted
            for row in get_partner_balances({**filters, "company": company})
        ]

    sale_where, sale_values = _filters_sql(filters, alias="sale")
    unreported = frappe.db.sql(
        f"""
        select
            sale.company,
            sale.supplier,
            sale.contract,
            sale.relationship_model,
            sale.currency,
            sum(sale.partner_amount - coalesce(ret.partner_amount, 0)) as unreported_amount,
            count(*) as unreported_allocations
        from `tabCC Sale Allocation` sale
        left join (
            select sale_allocation, sum(partner_amount) as partner_amount
            from `tabCC Sale Return Allocation`
            where status = 'RETURNED'
            group by sale_allocation
        ) ret on ret.sale_allocation = sale.name
        where sale.status in ('SOLD', 'PARTIALLY_RETURNED')
          and coalesce(sale.settlement_report, '') = ''
          and sale.partner_amount > 0
          {sale_where}
        group by sale.company, sale.supplier, sale.contract,
                 sale.relationship_model, sale.currency
        """,
        sale_values,
        as_dict=True,
    )
    report_where, report_values = _filters_sql(filters, alias="report")
    submitted = frappe.db.sql(
        f"""
        select
            report.company,
            report.supplier,
            report.contract,
            report.relationship_model,
            report.currency,
            sum(report.total_partner_amount) as gross_reported_amount,
            sum(report.adjusted_amount) as adjusted_amount,
            sum(report.net_partner_amount) as reported_amount,
            sum(report.paid_amount) as paid_amount,
            sum(report.outstanding_amount) as outstanding_amount,
            sum(report.partner_credit_amount) as partner_credit_amount,
            sum(
                case
                    when report.due_date < %(today)s and report.outstanding_amount > 0
                    then report.outstanding_amount else 0
                end
            ) as overdue_amount,
            count(*) as submitted_reports
        from `tabCC Settlement Report` report
        where report.docstatus = 1
          {report_where}
        group by report.company, report.supplier, report.contract,
                 report.relationship_model, report.currency
        """,
        {**report_values, "today": getdate(nowdate())},
        as_dict=True,
    )
    purchase_conditions = ["invoice.docstatus = 1", "own.docstatus = 1"]
    purchase_values: dict[str, Any] = {"today": getdate(nowdate())}
    for fieldname in ("company", "supplier"):
        if filters.get(fieldname):
            purchase_conditions.append(f"invoice.`{fieldname}` = %({fieldname})s")
            purchase_values[fieldname] = filters[fieldname]
    if filters.get("contract"):
        purchase_conditions.append("conversion.contract = %(contract)s")
        purchase_values["contract"] = filters["contract"]
    purchases = frappe.db.sql(
        f"""
        select
            invoice.company,
            invoice.supplier,
            coalesce(conversion.contract, '') as contract,
            concat('OWN/', own.source_method) as relationship_model,
            invoice.currency,
            sum(invoice.grand_total) as obligation_amount,
            sum(invoice.grand_total - invoice.outstanding_amount) as paid_amount,
            sum(invoice.outstanding_amount) as outstanding_amount,
            sum(
                case
                    when invoice.due_date < %(today)s and invoice.outstanding_amount > 0
                    then invoice.outstanding_amount else 0
                end
            ) as overdue_amount,
            count(*) as purchase_invoices
        from `tabPurchase Invoice` invoice
        inner join `tabCC Own Receipt` own on own.name = invoice.cc_own_receipt
        left join `tabCC Ownership Conversion` conversion
            on conversion.name = invoice.cc_ownership_conversion
        where {" and ".join(purchase_conditions)}
        group by invoice.company, invoice.supplier, conversion.contract,
                 own.source_method, invoice.currency
        """,
        purchase_values,
        as_dict=True,
    )
    rows: dict[tuple[str, ...], dict[str, Any]] = {}

    def key(row: Any) -> tuple[str, ...]:
        return (
            row.company,
            row.supplier,
            row.contract,
            row.relationship_model,
            row.currency,
        )

    for source in unreported:
        rows[key(source)] = {
            "company": source.company,
            "supplier": source.supplier,
            "contract": source.contract,
            "relationship_model": source.relationship_model,
            "currency": source.currency,
            "unreported_amount": _decimal(source.unreported_amount),
            "reported_amount": Decimal("0"),
            "gross_reported_amount": Decimal("0"),
            "adjusted_amount": Decimal("0"),
            "paid_amount": Decimal("0"),
            "outstanding_amount": Decimal("0"),
            "partner_credit_amount": Decimal("0"),
            "overdue_amount": Decimal("0"),
            "unreported_allocations": int(source.unreported_allocations or 0),
            "submitted_reports": 0,
            "purchase_invoices": 0,
        }
    for source in submitted:
        target = rows.setdefault(
            key(source),
            {
                "company": source.company,
                "supplier": source.supplier,
                "contract": source.contract,
                "relationship_model": source.relationship_model,
                "currency": source.currency,
                "unreported_amount": Decimal("0"),
                "reported_amount": Decimal("0"),
                "gross_reported_amount": Decimal("0"),
                "adjusted_amount": Decimal("0"),
                "paid_amount": Decimal("0"),
                "outstanding_amount": Decimal("0"),
                "partner_credit_amount": Decimal("0"),
                "overdue_amount": Decimal("0"),
                "unreported_allocations": 0,
                "submitted_reports": 0,
                "purchase_invoices": 0,
            },
        )
        for fieldname in (
            "reported_amount",
            "gross_reported_amount",
            "adjusted_amount",
            "paid_amount",
            "outstanding_amount",
            "partner_credit_amount",
            "overdue_amount",
        ):
            target[fieldname] = _decimal(source.get(fieldname))
        target["submitted_reports"] = int(source.submitted_reports or 0)
    for source in purchases:
        target = rows.setdefault(
            key(source),
            {
                "company": source.company,
                "supplier": source.supplier,
                "contract": source.contract,
                "relationship_model": source.relationship_model,
                "currency": source.currency,
                "unreported_amount": Decimal("0"),
                "reported_amount": Decimal("0"),
                "gross_reported_amount": Decimal("0"),
                "adjusted_amount": Decimal("0"),
                "paid_amount": Decimal("0"),
                "outstanding_amount": Decimal("0"),
                "partner_credit_amount": Decimal("0"),
                "overdue_amount": Decimal("0"),
                "unreported_allocations": 0,
                "submitted_reports": 0,
                "purchase_invoices": 0,
            },
        )
        obligation = _decimal(source.obligation_amount)
        target["gross_reported_amount"] += obligation
        target["reported_amount"] += obligation
        target["paid_amount"] += _decimal(source.paid_amount)
        target["outstanding_amount"] += _decimal(source.outstanding_amount)
        target["overdue_amount"] += _decimal(source.overdue_amount)
        target["purchase_invoices"] += int(source.purchase_invoices or 0)
    return [rows[value] for value in sorted(rows)]


def audit_financial_integrity(company: str | None = None) -> dict[str, Any]:
    """Reconcile immutable sales, returns, reports, payments and active reservations."""
    import frappe

    if company:
        _assert_company_permission(frappe, company)
    else:
        results = [
            audit_financial_integrity(value)
            for value in sorted(_permitted_companies(frappe))
        ]
        issues = [issue for result in results for issue in result["issues"]]
        checked: dict[str, int] = defaultdict(int)
        for result in results:
            for key, value in result["checked"].items():
                checked[key] += int(value)
        return {
            "ok": not issues,
            "company": None,
            "checked": dict(checked),
            "issue_count": len(issues),
            "issues": issues,
        }

    issues: list[dict[str, Any]] = []
    checked = defaultdict(int)

    def issue(code: str, doctype: str, name: str, message: str) -> None:
        issues.append(
            {
                "code": code,
                "doctype": doctype,
                "name": name,
                "message": message,
            }
        )

    expected_024, ledger_024 = _psbo_024_totals(frappe, company)
    for key in sorted(set(expected_024) | set(ledger_024)):
        checked["account_024_groups"] += 1
        expected_qty, expected_amount = expected_024.get(
            key,
            (Decimal("0"), Decimal("0")),
        )
        ledger_qty, ledger_amount = ledger_024.get(
            key,
            (Decimal("0"), Decimal("0")),
        )
        if (
            abs(expected_qty - ledger_qty) > Decimal("0.000001")
            or abs(expected_amount - ledger_amount) > Decimal("0.000001")
        ):
            uom, currency = key
            issue(
                "ACCOUNT_024_BALANCE",
                "UA Off Balance Entry",
                f"{company}:{uom}:{currency}",
                "operational third-party balance differs from the account-024 ledger",
            )

    receipt_rows = frappe.db.sql(
        """
        select item.name, item.off_balance_entry
        from `tabCC Receipt Item` item
        inner join `tabCC Receipt` receipt on receipt.name = item.parent
        where receipt.company = %s and receipt.docstatus = 1
        """,
        (company,),
        as_dict=True,
    )
    for row in receipt_rows:
        checked["account_024_receipts"] += 1
        if not row.off_balance_entry or frappe.db.get_value(
            "UA Off Balance Entry",
            row.off_balance_entry,
            "docstatus",
        ) != 1:
            issue(
                "ACCOUNT_024_RECEIPT",
                "CC Receipt Item",
                row.name,
                "submitted receipt row has no active account-024 increase",
            )

    sale_filters = {"company": company} if company else None
    returns = {
        row.sale_allocation: row
        for row in frappe.db.sql(
            """
            select sale_allocation, sum(returned_qty) as returned_qty,
                   sum(partner_amount) as partner_amount
            from `tabCC Sale Return Allocation`
            where status = 'RETURNED'
            group by sale_allocation
            """,
            as_dict=True,
        )
    }
    sales = frappe.get_all(
        "CC Sale Allocation",
        filters=sale_filters,
        fields=[
            "name",
            "status",
            "source_method",
            "relationship_model",
            "net_amount",
            "base_net_amount",
            "commission_amount",
            "base_commission_amount",
            "partner_amount",
            "base_partner_amount",
            "retained_amount",
            "base_retained_amount",
            "sold_qty",
            "returned_qty",
            "settlement_report",
            "recognition_journal_entry",
            "off_balance_amount",
            "off_balance_entry",
        ],
    )
    for sale in sales:
        checked["sale_allocations"] += 1
        gross = _decimal(sale.net_amount)
        commission = _decimal(sale.commission_amount)
        partner = _decimal(sale.partner_amount)
        retained = _decimal(sale.retained_amount)
        base_gross = _decimal(sale.base_net_amount)
        base_commission = _decimal(sale.base_commission_amount)
        base_partner = _decimal(sale.base_partner_amount)
        base_retained = _decimal(sale.base_retained_amount)
        if SOURCE_METHOD_RELATIONSHIP_MODEL.get(sale.source_method) != sale.relationship_model:
            issue("SALE_MODEL", "CC Sale Allocation", sale.name, "source method/model mismatch")
        if gross != partner + retained:
            issue("SALE_BALANCE", "CC Sale Allocation", sale.name, "net != partner + retained")
        if base_gross != base_partner + base_retained:
            issue(
                "SALE_BASE_BALANCE",
                "CC Sale Allocation",
                sale.name,
                "base net != base partner + base retained",
            )
        if sale.relationship_model == "COMMISSION" and commission != retained:
            issue("SALE_COMMISSION", "CC Sale Allocation", sale.name, "commission != retained")
        if sale.relationship_model == "COMMISSION" and base_commission != base_retained:
            issue(
                "SALE_BASE_COMMISSION",
                "CC Sale Allocation",
                sale.name,
                "base commission != base retained",
            )
        returned = returns.get(sale.name)
        audited_qty = _decimal(returned.returned_qty) if returned else Decimal("0")
        if audited_qty != _decimal(sale.returned_qty):
            issue(
                "RETURN_QTY",
                "CC Sale Allocation",
                sale.name,
                "active return audit quantity differs from returned_qty",
            )
        if audited_qty < 0 or audited_qty > _decimal(sale.sold_qty):
            issue("RETURN_RANGE", "CC Sale Allocation", sale.name, "returned quantity is invalid")
        if sale.status == "REPORTED" and not sale.settlement_report:
            issue("REPORT_LINK", "CC Sale Allocation", sale.name, "REPORTED sale has no report")
        if sale.relationship_model != "OWN" and sale.status != "CANCELLED":
            if _decimal(sale.off_balance_amount) <= 0 or frappe.db.get_value(
                "UA Off Balance Entry",
                sale.off_balance_entry,
                "docstatus",
            ) != 1:
                issue(
                    "ACCOUNT_024_SALE",
                    "CC Sale Allocation",
                    sale.name,
                    "active third-party sale has no matching account-024 decrease",
                )
            if not sale.recognition_journal_entry:
                issue("RECOGNITION_LINK", "CC Sale Allocation", sale.name, "recognition JE is missing")
            elif frappe.db.get_value(
                "Journal Entry",
                sale.recognition_journal_entry,
                "docstatus",
            ) != 1:
                issue(
                    "RECOGNITION_STATUS",
                    "Journal Entry",
                    sale.recognition_journal_entry,
                    "active sale recognition JE is not submitted",
                )

    for audit in frappe.db.sql(
        """
        select audit.name, audit.off_balance_amount, audit.off_balance_entry
        from `tabCC Sale Return Allocation` audit
        inner join `tabSales Invoice` invoice
            on invoice.name = audit.return_sales_invoice and invoice.docstatus = 1
        where audit.company = %s
          and audit.relationship_model in ('COMMISSION', 'CONSIGNMENT')
        """,
        (company,),
        as_dict=True,
    ):
        checked["account_024_returns"] += 1
        if _decimal(audit.off_balance_amount) <= 0 or frappe.db.get_value(
            "UA Off Balance Entry",
            audit.off_balance_entry,
            "docstatus",
        ) != 1:
            issue(
                "ACCOUNT_024_RETURN",
                "CC Sale Return Allocation",
                audit.name,
                "submitted customer return has no matching account-024 increase",
            )

    report_filters: dict[str, Any] = {"docstatus": 1}
    if company:
        report_filters["company"] = company
    report_item_rows = frappe.db.sql(
        """
        select parent, sum(partner_amount) as total,
               sum(base_partner_amount) as base_total
        from `tabCC Settlement Report Item`
        group by parent
        """,
        as_dict=True,
    )
    item_totals = {row.parent: _decimal(row.total) for row in report_item_rows}
    item_base_totals = {row.parent: _decimal(row.base_total) for row in report_item_rows}
    payment_totals = {
        row.report: _decimal(row.total)
        for row in frappe.db.sql(
            f"""
            select `{SETTLEMENT_REPORT_FIELD}` as report, sum(received_amount) as total
            from `tabPayment Entry`
            where docstatus = 1 and coalesce(`{SETTLEMENT_REPORT_FIELD}`, '') != ''
            group by `{SETTLEMENT_REPORT_FIELD}`
            """,
            as_dict=True,
        )
    }
    adjustment_rows = frappe.get_all(
        "CC Settlement Adjustment",
        filters={"docstatus": 1},
        fields=[
            "name",
            "settlement_report",
            "amount",
            "base_amount",
            "journal_entry",
        ],
    )
    adjustment_totals: dict[str, Decimal] = defaultdict(Decimal)
    adjustment_base_totals: dict[str, Decimal] = defaultdict(Decimal)
    for adjustment in adjustment_rows:
        checked["settlement_adjustments"] += 1
        adjustment_totals[adjustment.settlement_report] += _decimal(adjustment.amount)
        adjustment_base_totals[adjustment.settlement_report] += _decimal(
            adjustment.base_amount
        )
        if not adjustment.journal_entry or frappe.db.get_value(
            "Journal Entry",
            adjustment.journal_entry,
            "docstatus",
        ) != 1:
            issue(
                "ADJUSTMENT_JE",
                "CC Settlement Adjustment",
                adjustment.name,
                "adjustment JE is missing or not submitted",
            )
    for report in frappe.get_all(
        "CC Settlement Report",
        filters=report_filters,
        fields=[
            "name",
            "status",
            "total_partner_amount",
            "base_total_partner_amount",
            "adjusted_amount",
            "base_adjusted_amount",
            "net_partner_amount",
            "paid_amount",
            "outstanding_amount",
            "partner_credit_amount",
            "debt_journal_entry",
        ],
    ):
        checked["settlement_reports"] += 1
        total = _decimal(report.total_partner_amount)
        base_total = _decimal(report.base_total_partner_amount)
        adjusted = _decimal(report.adjusted_amount)
        base_adjusted = _decimal(report.base_adjusted_amount)
        net = _decimal(report.net_partner_amount)
        paid = _decimal(report.paid_amount)
        outstanding = _decimal(report.outstanding_amount)
        credit = _decimal(report.partner_credit_amount)
        if item_totals.get(report.name, Decimal("0")) != total:
            issue("REPORT_ITEMS", "CC Settlement Report", report.name, "item total mismatch")
        if base_total <= 0 or item_base_totals.get(report.name, Decimal("0")) != base_total:
            issue("REPORT_BASE", "CC Settlement Report", report.name, "base item total mismatch")
        if adjustment_totals[report.name] != adjusted:
            issue("REPORT_ADJUSTMENTS", "CC Settlement Report", report.name, "adjustment total mismatch")
        if adjustment_base_totals[report.name] != base_adjusted:
            issue(
                "REPORT_BASE_ADJUSTMENTS",
                "CC Settlement Report",
                report.name,
                "base adjustment total mismatch",
            )
        if net != total - adjusted:
            issue("REPORT_NET", "CC Settlement Report", report.name, "net report amount mismatch")
        if paid + outstanding != net + credit:
            issue(
                "REPORT_BALANCE",
                "CC Settlement Report",
                report.name,
                "paid + outstanding != net + partner credit",
            )
        if payment_totals.get(report.name, Decimal("0")) != paid:
            issue("PAYMENT_BALANCE", "CC Settlement Report", report.name, "submitted payment sum mismatch")
        if not report.debt_journal_entry or frappe.db.get_value(
            "Journal Entry",
            report.debt_journal_entry,
            "docstatus",
        ) != 1:
            issue("DEBT_JE", "CC Settlement Report", report.name, "debt JE is missing or not submitted")

    own_filters: dict[str, Any] = {"docstatus": 1}
    if company:
        own_filters["company"] = company
    for receipt in frappe.get_all(
        "CC Own Receipt",
        filters=own_filters,
        fields=["name", "purchase_invoice", "total_amount"],
    ):
        checked["own_receipts"] += 1
        invoice = (
            frappe.db.get_value(
                "Purchase Invoice",
                receipt.purchase_invoice,
                ["docstatus", "cc_own_receipt", "grand_total", "outstanding_amount"],
                as_dict=True,
            )
            if receipt.purchase_invoice
            else None
        )
        if not invoice or invoice.docstatus != 1 or invoice.cc_own_receipt != receipt.name:
            issue(
                "OWN_PURCHASE_LINK",
                "CC Own Receipt",
                receipt.name,
                "submitted own receipt has no matching submitted Purchase Invoice",
            )
            continue
        total = _decimal(invoice.grand_total)
        outstanding = _decimal(invoice.outstanding_amount)
        if total != _decimal(receipt.total_amount):
            issue("OWN_PURCHASE_TOTAL", "CC Own Receipt", receipt.name, "purchase total mismatch")
        if outstanding < 0 or outstanding > total:
            issue(
                "OWN_PURCHASE_OUTSTANDING",
                "Purchase Invoice",
                receipt.purchase_invoice,
                "outstanding amount is outside the purchase obligation",
            )
        gl = frappe.db.sql(
            """
            select coalesce(sum(debit), 0) as debit, coalesce(sum(credit), 0) as credit
            from `tabGL Entry`
            where voucher_type = 'Purchase Invoice' and voucher_no = %s and is_cancelled = 0
            """,
            (receipt.purchase_invoice,),
            as_dict=True,
        )[0]
        if _decimal(gl.debit) != _decimal(gl.credit) or _decimal(gl.debit) <= 0:
            issue(
                "OWN_PURCHASE_GL",
                "Purchase Invoice",
                receipt.purchase_invoice,
                "active purchase GL is missing or unbalanced",
            )

    conversion_filters: dict[str, Any] = {"docstatus": 1}
    return_filters: dict[str, Any] = {"docstatus": 1}
    if company:
        conversion_filters["company"] = company
        return_filters["company"] = company
    for conversion in frappe.get_all(
        "CC Ownership Conversion",
        filters=conversion_filters,
        fields=[
            "name",
            "source_lot",
            "qty",
            "source_issue",
            "own_receipt",
            "target_lot",
            "purchase_invoice",
            "off_balance_amount",
            "off_balance_entry",
        ],
    ):
        checked["ownership_conversions"] += 1
        if _decimal(conversion.off_balance_amount) <= 0 or frappe.db.get_value(
            "UA Off Balance Entry",
            conversion.off_balance_entry,
            "docstatus",
        ) != 1:
            issue(
                "ACCOUNT_024_CONVERSION",
                "CC Ownership Conversion",
                conversion.name,
                "submitted ownership conversion has no matching account-024 decrease",
            )
        linked = {
            "source_issue": (
                "Stock Entry",
                conversion.source_issue,
                OWNERSHIP_CONVERSION_FIELD,
            ),
            "own_receipt": ("CC Own Receipt", conversion.own_receipt, "ownership_conversion"),
            "purchase_invoice": (
                "Purchase Invoice",
                conversion.purchase_invoice,
                OWNERSHIP_CONVERSION_FIELD,
            ),
        }
        for label, (doctype, name, backlink) in linked.items():
            evidence = (
                frappe.db.get_value(doctype, name, ["docstatus", backlink], as_dict=True)
                if name
                else None
            )
            if not evidence or evidence.docstatus != 1 or evidence.get(backlink) != conversion.name:
                issue(
                    "CONVERSION_LINK",
                    "CC Ownership Conversion",
                    conversion.name,
                    f"{label} is missing, not submitted or belongs to another conversion",
                )
        target = frappe.db.get_value(
            "CC Stock Lot",
            conversion.target_lot,
            ["relationship_model", "ownership_conversion"],
            as_dict=True,
        ) if conversion.target_lot else None
        if (
            not target
            or target.relationship_model != "OWN"
            or target.ownership_conversion != conversion.name
        ):
            issue(
                "CONVERSION_TARGET",
                "CC Ownership Conversion",
                conversion.name,
                "target lot is missing or is not the linked OWN lot",
            )
        issue_qty = _decimal(
            frappe.db.sql(
                f"""
                select coalesce(sum(actual_qty), 0)
                from `tabStock Ledger Entry`
                where voucher_type = 'Stock Entry' and voucher_no = %s
                  and is_cancelled = 0 and `{OWNERSHIP_FIELD}` = %s
                """,
                (conversion.source_issue, conversion.source_lot),
            )[0][0]
        )
        if issue_qty != -_decimal(conversion.qty):
            issue(
                "CONVERSION_ISSUE_QTY",
                "CC Ownership Conversion",
                conversion.name,
                "source Material Issue quantity differs from the conversion quantity",
            )
        target_qty = _decimal(
            frappe.db.sql(
                f"""
                select coalesce(sum(actual_qty), 0)
                from `tabStock Ledger Entry`
                where voucher_type = 'Purchase Invoice' and voucher_no = %s
                  and is_cancelled = 0 and `{OWNERSHIP_FIELD}` = %s
                """,
                (conversion.purchase_invoice, conversion.target_lot),
            )[0][0]
        )
        if target_qty != _decimal(conversion.qty):
            issue(
                "CONVERSION_RECEIPT_QTY",
                "CC Ownership Conversion",
                conversion.name,
                "target Purchase Invoice quantity differs from the conversion quantity",
            )
        if frappe.db.exists(
            "GL Entry",
            {
                "voucher_type": "Stock Entry",
                "voucher_no": conversion.source_issue,
                "is_cancelled": 0,
            },
        ):
            issue(
                "CONVERSION_ISSUE_GL",
                "CC Ownership Conversion",
                conversion.name,
                "zero-value conversion issue unexpectedly created GL entries",
            )

    for partner_return in frappe.get_all(
        "CC Partner Return",
        filters=return_filters,
        fields=[
            "name",
            "source_lot",
            "qty",
            "stock_entry",
            "off_balance_amount",
            "off_balance_entry",
        ],
    ):
        checked["partner_returns"] += 1
        if _decimal(partner_return.off_balance_amount) <= 0 or frappe.db.get_value(
            "UA Off Balance Entry",
            partner_return.off_balance_entry,
            "docstatus",
        ) != 1:
            issue(
                "ACCOUNT_024_PARTNER_RETURN",
                "CC Partner Return",
                partner_return.name,
                "submitted partner return has no matching account-024 decrease",
            )
        stock_entry = frappe.db.get_value(
            "Stock Entry",
            partner_return.stock_entry,
            ["docstatus", PARTNER_RETURN_FIELD],
            as_dict=True,
        ) if partner_return.stock_entry else None
        if (
            not stock_entry
            or stock_entry.docstatus != 1
            or stock_entry.get(PARTNER_RETURN_FIELD) != partner_return.name
        ):
            issue(
                "PARTNER_RETURN_LINK",
                "CC Partner Return",
                partner_return.name,
                "partner return has no matching submitted Material Issue",
            )
            continue
        returned_qty = _decimal(
            frappe.db.sql(
                f"""
                select coalesce(sum(actual_qty), 0)
                from `tabStock Ledger Entry`
                where voucher_type = 'Stock Entry' and voucher_no = %s
                  and is_cancelled = 0 and `{OWNERSHIP_FIELD}` = %s
                """,
                (partner_return.stock_entry, partner_return.source_lot),
            )[0][0]
        )
        if returned_qty != -_decimal(partner_return.qty):
            issue(
                "PARTNER_RETURN_QTY",
                "CC Partner Return",
                partner_return.name,
                "Material Issue quantity differs from the partner return quantity",
            )
        if frappe.db.exists(
            "GL Entry",
            {
                "voucher_type": "Stock Entry",
                "voucher_no": partner_return.stock_entry,
                "is_cancelled": 0,
            },
        ):
            issue(
                "PARTNER_RETURN_GL",
                "CC Partner Return",
                partner_return.name,
                "zero-value partner return unexpectedly created GL entries",
            )

    reserved = {
        row.stock_lot: _decimal(row.qty)
        for row in frappe.db.sql(
            """
            select slice.stock_lot, sum(slice.qty) as qty
            from `tabCC Allocation Slice` slice
            inner join `tabCC Allocation` allocation on allocation.name = slice.parent
            where allocation.status = 'RESERVED'
            group by slice.stock_lot
            """,
            as_dict=True,
        )
    }
    lot_filters = {"company": company} if company else None
    for lot in frappe.get_all("CC Stock Lot", filters=lot_filters, fields=["name", "reserved_qty"]):
        checked["stock_lots"] += 1
        if _decimal(lot.reserved_qty) != reserved.get(lot.name, Decimal("0")):
            issue(
                "RESERVATION_BALANCE",
                "CC Stock Lot",
                lot.name,
                "reserved_qty differs from active allocation slices",
            )

    return {
        "ok": not issues,
        "company": company,
        "checked": dict(checked),
        "issue_count": len(issues),
        "issues": issues,
    }
