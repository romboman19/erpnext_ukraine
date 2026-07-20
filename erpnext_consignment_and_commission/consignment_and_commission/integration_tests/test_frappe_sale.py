"""Clean-site controlled Sales Invoice and reservation-consumption lifecycle."""

from __future__ import annotations

from decimal import Decimal

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, get_datetime, getdate, nowdate

from erpnext_consignment_and_commission.consignment_and_commission.integrations.payments import (
    create_settlement_payment,
    submit_settlement_payment,
)
from erpnext_consignment_and_commission.consignment_and_commission.integrations.reconciliation import (
    audit_financial_integrity,
)
from erpnext_consignment_and_commission.consignment_and_commission.integrations.reporting import (
    get_fifo_inventory,
    get_sale_financials,
)
from erpnext_consignment_and_commission.consignment_and_commission.integrations.reservations import (
    release_allocation,
    reserve_stock,
)
from erpnext_consignment_and_commission.consignment_and_commission.integrations.sale_returns import (
    create_return_invoice,
)
from erpnext_consignment_and_commission.consignment_and_commission.integrations.sales_invoice import (
    _resolve_selling_price_list,
    create_sales_invoice_from_allocations,
)
from erpnext_consignment_and_commission.consignment_and_commission.integrations.settlements import (
    cancel_settlement_report,
    create_settlement_report,
    submit_settlement_report,
)
from erpnext_consignment_and_commission.consignment_and_commission.services.payment import (
    SettlementPaymentError,
    SettlementPaymentRequest,
)
from erpnext_consignment_and_commission.consignment_and_commission.services.reservation import (
    ReservationRequest,
)
from erpnext_consignment_and_commission.consignment_and_commission.services.sale import (
    ManagedSaleLine,
    ManagedSaleRequest,
)
from erpnext_consignment_and_commission.consignment_and_commission.services.sale_return import (
    ManagedReturnLine,
    ManagedReturnRequest,
)
from erpnext_consignment_and_commission.consignment_and_commission.services.settlement import (
    SettlementRequest,
)
from erpnext_consignment_and_commission.consignment_and_commission.services.stock_lot import (
    get_ownership_balance,
)
from erpnext_consignment_and_commission.consignment_and_commission.setup.ownership_dimension import (
    ALLOCATION_FIELD,
    MANAGED_SALE_FIELD,
    OWNERSHIP_FIELD,
    POSTING_KIND_FIELD,
    RETURN_IDEMPOTENCY_FIELD,
    SALE_IDEMPOTENCY_FIELD,
    SALES_INVOICE_FIELD,
)

from .test_frappe_foundation import (
    COMPANY,
    LOCATION,
    PARTNER,
    _cleanup_integration_records,
)
from .test_frappe_own_receipt import _cleanup_own_receipts
from .test_frappe_pricing import _cleanup_price_versions
from .test_frappe_receipt import (
    _cleanup_off_balance_records,
    _cleanup_receipt_records,
    _ensure_item,
    _ensure_receipt_context,
)
from .test_frappe_reservation import _cleanup_allocations

CUSTOMER = "_CC Integration Customer"
CUSTOMER_GROUP = "_CC Integration Customers"
CUSTOMER_GROUP_ROOT = "_CC Integration All Customers"
TERRITORY = "_CC Integration Territory"
TERRITORY_ROOT = "_CC Integration All Territories"
_created_customer_nodes: set[tuple[str, str]] = set()
_created_party_types: set[str] = set()
SELLING_PRICE_LISTS = {
    "USD": "_CC Integration Selling USD",
    "EUR": "_CC Integration Selling EUR",
}
_created_selling_price_lists: set[str] = set()


def _ensure_party_types() -> None:
    expected = {"Customer": "Receivable", "Supplier": "Payable"}
    for party_type, account_type in expected.items():
        existing = frappe.db.get_value("Party Type", party_type, "account_type")
        if existing:
            if existing != account_type:
                raise AssertionError(
                    f"Party Type {party_type} must use account type {account_type}"
                )
            continue
        frappe.get_doc(
            {
                "doctype": "Party Type",
                "party_type": party_type,
                "account_type": account_type,
            }
        ).insert(ignore_permissions=True)
        _created_party_types.add(party_type)


def _ensure_selling_price_lists() -> None:
    for currency, name in SELLING_PRICE_LISTS.items():
        if frappe.db.exists("Price List", name):
            continue
        if not frappe.db.exists("Currency", currency):
            raise AssertionError(f"Integration currency {currency} does not exist")
        frappe.get_doc(
            {
                "doctype": "Price List",
                "price_list_name": name,
                "enabled": 1,
                "selling": 1,
                "buying": 0,
                "currency": currency,
            }
        ).insert(ignore_permissions=True)
        _created_selling_price_lists.add(name)


def _ensure_leaf_node(
    doctype: str,
    *,
    name_field: str,
    parent_field: str,
    root_name: str,
    leaf_name: str,
) -> str:
    if frappe.db.exists(doctype, leaf_name):
        return leaf_name
    roots = frappe.get_all(
        doctype,
        filters={"is_group": 1},
        pluck="name",
        order_by="lft asc",
        limit=1,
    )
    if roots:
        root = roots[0]
    else:
        root = frappe.get_doc(
            {
                "doctype": doctype,
                name_field: root_name,
                "is_group": 1,
            }
        ).insert(ignore_permissions=True).name
        _created_customer_nodes.add((doctype, root))
    leaf = frappe.get_doc(
        {
            "doctype": doctype,
            name_field: leaf_name,
            parent_field: root,
            "is_group": 0,
        }
    ).insert(ignore_permissions=True).name
    _created_customer_nodes.add((doctype, leaf))
    return leaf


def _ensure_customer() -> str:
    _ensure_party_types()
    _ensure_selling_price_lists()
    existing = frappe.db.get_value("Customer", {"customer_name": CUSTOMER}, "name")
    if existing:
        return existing
    customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name") or _ensure_leaf_node(
        "Customer Group",
        name_field="customer_group_name",
        parent_field="parent_customer_group",
        root_name=CUSTOMER_GROUP_ROOT,
        leaf_name=CUSTOMER_GROUP,
    )
    territory = frappe.db.get_value("Territory", {"is_group": 0}, "name") or _ensure_leaf_node(
        "Territory",
        name_field="territory_name",
        parent_field="parent_territory",
        root_name=TERRITORY_ROOT,
        leaf_name=TERRITORY,
    )
    return frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": CUSTOMER,
            "customer_type": "Individual",
            "customer_group": customer_group,
            "territory": territory,
        }
    ).insert(ignore_permissions=True).name


def _cleanup_settlements() -> None:
    if not frappe.db.exists("DocType", "CC Settlement Report"):
        return
    reports = frappe.get_all(
        "CC Settlement Report",
        filters={"company": COMPANY},
        pluck="name",
    )
    adjustments = frappe.get_all(
        "CC Settlement Adjustment",
        filters={"settlement_report": ("in", reports)},
        fields=["name", "journal_entry"],
    ) if reports and frappe.db.exists("DocType", "CC Settlement Adjustment") else []
    journals = frappe.get_all(
        "Journal Entry",
        filters={"cc_settlement_report": ("in", reports)},
        pluck="name",
    ) if reports else []
    journals.extend(row.journal_entry for row in adjustments if row.journal_entry)
    payments = frappe.get_all(
        "Payment Entry",
        filters={"cc_settlement_report": ("in", reports)},
        pluck="name",
    ) if reports else []
    for name in payments:
        payment = frappe.get_doc("Payment Entry", name)
        if payment.docstatus == 1:
            payment.cancel()
    for name in payments:
        if frappe.db.exists("Payment Entry", name):
            frappe.delete_doc("Payment Entry", name, force=True, ignore_permissions=True)
    if adjustments:
        from ..integrations.settlement_adjustments import _adjustment_write

        for row in adjustments:
            adjustment = frappe.get_doc("CC Settlement Adjustment", row.name)
            if adjustment.docstatus == 1:
                with _adjustment_write(frappe):
                    adjustment.cancel()
    if adjustments:
        from ..doctype.cc_settlement_adjustment.cc_settlement_adjustment import (
            TEST_CLEANUP_FLAG as ADJUSTMENT_CLEANUP_FLAG,
        )

        previous_adjustment = getattr(frappe.flags, ADJUSTMENT_CLEANUP_FLAG, False)
        setattr(frappe.flags, ADJUSTMENT_CLEANUP_FLAG, True)
        try:
            for row in adjustments:
                if frappe.db.exists("CC Settlement Adjustment", row.name):
                    frappe.delete_doc(
                        "CC Settlement Adjustment",
                        row.name,
                        force=True,
                        ignore_permissions=True,
                    )
        finally:
            setattr(frappe.flags, ADJUSTMENT_CLEANUP_FLAG, previous_adjustment)
    for name in reports:
        report = frappe.get_doc("CC Settlement Report", name)
        if report.docstatus == 1 and not report.paid_amount:
            cancel_settlement_report(name)
    from ..doctype.cc_settlement_report.cc_settlement_report import TEST_CLEANUP_FLAG

    previous = getattr(frappe.flags, TEST_CLEANUP_FLAG, False)
    setattr(frappe.flags, TEST_CLEANUP_FLAG, True)
    try:
        for name in reports:
            if frappe.db.exists("CC Settlement Report", name):
                frappe.delete_doc(
                    "CC Settlement Report",
                    name,
                    force=True,
                    ignore_permissions=True,
                )
    finally:
        setattr(frappe.flags, TEST_CLEANUP_FLAG, previous)
    for name in journals:
        if frappe.db.exists("Journal Entry", name):
            journal = frappe.get_doc("Journal Entry", name)
            if journal.docstatus == 1:
                from ..integrations.settlements import _settlement_cancellation

                ignored = set(journal.get("ignore_linked_doctypes") or ())
                ignored.add("CC Settlement Report")
                journal.ignore_linked_doctypes = tuple(sorted(ignored))
                with _settlement_cancellation(frappe):
                    journal.cancel()
            frappe.delete_doc("Journal Entry", name, force=True, ignore_permissions=True)


def _cleanup_managed_sales() -> None:
    _cleanup_off_balance_records()
    if not frappe.db.has_column("Sales Invoice", SALE_IDEMPOTENCY_FIELD):
        return
    _cleanup_settlements()
    sale_names = frappe.get_all(
        "Sales Invoice",
        filters={SALE_IDEMPOTENCY_FIELD: ("is", "set")},
        pluck="name",
    )
    return_names = frappe.get_all(
        "Sales Invoice",
        filters={RETURN_IDEMPOTENCY_FIELD: ("is", "set")},
        pluck="name",
    )
    names = [*return_names, *sale_names]
    for name in names:
        invoice = frappe.get_doc("Sales Invoice", name)
        if invoice.docstatus == 1:
            invoice.cancel()
    journals = frappe.get_all(
        "Journal Entry",
        filters={SALES_INVOICE_FIELD: ("in", names)},
        pluck="name",
    ) if names else []
    if frappe.db.exists("DocType", "CC Sale Return Allocation"):
        from ..doctype.cc_sale_return_allocation.cc_sale_return_allocation import (
            TEST_CLEANUP_FLAG as RETURN_CLEANUP_FLAG,
        )

        previous_return = getattr(frappe.flags, RETURN_CLEANUP_FLAG, False)
        setattr(frappe.flags, RETURN_CLEANUP_FLAG, True)
        try:
            for name in frappe.get_all(
                "CC Sale Return Allocation",
                filters={"company": COMPANY},
                pluck="name",
            ):
                frappe.delete_doc(
                    "CC Sale Return Allocation",
                    name,
                    force=True,
                    ignore_permissions=True,
                )
        finally:
            setattr(frappe.flags, RETURN_CLEANUP_FLAG, previous_return)
    if frappe.db.exists("DocType", "CC Sale Allocation"):
        from ..doctype.cc_sale_allocation.cc_sale_allocation import (
            TEST_CLEANUP_FLAG,
        )

        previous = getattr(frappe.flags, TEST_CLEANUP_FLAG, False)
        setattr(frappe.flags, TEST_CLEANUP_FLAG, True)
        try:
            for name in frappe.get_all(
                "CC Sale Allocation",
                filters={"company": COMPANY},
                pluck="name",
            ):
                frappe.delete_doc(
                    "CC Sale Allocation",
                    name,
                    force=True,
                    ignore_permissions=True,
                )
        finally:
            setattr(frappe.flags, TEST_CLEANUP_FLAG, previous)
    _cleanup_allocations()
    for name in journals:
        if frappe.db.exists("Journal Entry", name):
            frappe.delete_doc("Journal Entry", name, force=True, ignore_permissions=True)
    for name in names:
        if frappe.db.exists("Sales Invoice", name):
            frappe.delete_doc("Sales Invoice", name, force=True, ignore_permissions=True)
    if frappe.db.exists("Customer", CUSTOMER):
        frappe.delete_doc("Customer", CUSTOMER, force=True, ignore_permissions=True)
    for doctype, name in (
        ("Customer Group", CUSTOMER_GROUP),
        ("Territory", TERRITORY),
        ("Customer Group", CUSTOMER_GROUP_ROOT),
        ("Territory", TERRITORY_ROOT),
    ):
        if (doctype, name) in _created_customer_nodes and frappe.db.exists(doctype, name):
            frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
            _created_customer_nodes.discard((doctype, name))
    for name in tuple(_created_selling_price_lists):
        if frappe.db.exists("Price List", name):
            frappe.delete_doc("Price List", name, force=True, ignore_permissions=True)
        _created_selling_price_lists.discard(name)
    for name in tuple(_created_party_types):
        if frappe.db.exists("Party Type", name):
            frappe.delete_doc("Party Type", name, force=True, ignore_permissions=True)
        _created_party_types.discard(name)
    frappe.db.commit()
    frappe.clear_cache()


def _ensure_account_mapping(company: frappe.model.document.Document) -> tuple[object, dict[str, str]]:
    from erpnext_consignment_and_commission.consignment_and_commission.spikes.accounting import (
        _ensure_accounts,
    )

    accounts = _ensure_accounts(frappe, company.name, require_payment_accounts=False)
    if frappe.db.exists("CC Account Mapping", company.name):
        mapping = frappe.get_doc("CC Account Mapping", company.name)
    else:
        mapping = frappe.get_doc(
            {
                "doctype": "CC Account Mapping",
                "company": company.name,
                "off_balance_goods_account": accounts["off_balance_goods"],
                "gross_proceeds_clearing_account": accounts["commission_gross_proceeds"],
                "commission_revenue_account": accounts["commission_revenue"],
                "principal_proceeds_deduction_account": accounts[
                    "principal_proceeds_deduction"
                ],
                "unreported_commission_liability_account": accounts[
                    "unreported_commission_liability"
                ],
                "unreported_consignment_liability_account": accounts[
                    "unreported_consignment_liability"
                ],
                "default_supplier_payable_account": accounts["supplier_payable"],
            }
        ).insert(ignore_permissions=True)
    mapping.run_method("validate")
    return mapping, accounts


def _unmanaged_invoice(
    company: frappe.model.document.Document,
    customer: str,
    item: str,
    warehouse: str,
) -> frappe.model.document.Document:
    from erpnext.accounts.party import get_party_account

    price_list = _resolve_selling_price_list(
        frappe,
        customer,
        company.default_currency,
    )

    invoice = frappe.get_doc(
        {
            "doctype": "Sales Invoice",
            "company": company.name,
            "customer": customer,
            "posting_date": nowdate(),
            "due_date": nowdate(),
            "update_stock": 1,
            "currency": company.default_currency,
            "conversion_rate": 1,
            "selling_price_list": price_list,
            "price_list_currency": company.default_currency,
            "plc_conversion_rate": 1,
            "debit_to": get_party_account("Customer", customer, company.name),
            "items": [
                {
                    "item_code": item,
                    "warehouse": warehouse,
                    "qty": 1,
                    "uom": "Nos",
                    "stock_uom": "Nos",
                    "conversion_factor": 1,
                    "rate": 100,
                    "price_list_rate": 100,
                    "income_account": company.default_income_account,
                    "expense_account": company.default_expense_account,
                    "cost_center": company.cost_center,
                }
            ],
        }
    )
    return invoice.insert(ignore_permissions=True)


class TestFrappeSale(IntegrationTestCase):
    def test_foreign_currency_sale_debt_partial_payments_and_exchange_difference(self) -> None:
        _cleanup_managed_sales()
        _cleanup_receipt_records()
        _cleanup_integration_records()
        self.addCleanup(_cleanup_integration_records)
        self.addCleanup(_cleanup_receipt_records)
        self.addCleanup(_cleanup_managed_sales)

        company, warehouses, commission_contract_name = _ensure_receipt_context()
        mapping, _accounts = _ensure_account_mapping(company)
        from erpnext_consignment_and_commission.consignment_and_commission.spikes.accounting import (
            _ensure_account,
        )

        payable_eur = _ensure_account(
            frappe,
            company=company.name,
            account_name="_CC Integration Creditors EUR",
            parent_name="Accounts Payable",
            currency="EUR",
            account_type="Payable",
        )
        settlement_bank = _ensure_account(
            frappe,
            company=company.name,
            account_name="_CC Integration Settlement Bank",
            parent_name="Bank Accounts",
            currency=company.default_currency,
            account_type="Bank",
        )
        receivable_eur = _ensure_account(
            frappe,
            company=company.name,
            account_name="_CC Integration Debtors EUR",
            parent_name="Accounts Receivable",
            currency="EUR",
            account_type="Receivable",
        )
        supplier_name = frappe.db.get_value(
            "CC Partner Profile",
            PARTNER,
            "supplier",
        )
        supplier = frappe.get_doc("Supplier", supplier_name)
        supplier.default_currency = "EUR"
        account_row = next(row for row in supplier.accounts if row.company == company.name)
        account_row.account = payable_eur
        supplier.save(ignore_permissions=True)
        partner = frappe.get_doc("CC Partner Profile", PARTNER)
        partner.default_currency = "EUR"
        partner.save(ignore_permissions=True)
        contract = frappe.get_doc("CC Contract", commission_contract_name)
        contract.currency = "EUR"
        contract.save(ignore_permissions=True)

        customer = _ensure_customer()
        customer_doc = frappe.get_doc("Customer", customer)
        customer_doc.default_currency = "EUR"
        customer_account = next(
            (row for row in customer_doc.accounts if row.company == company.name),
            None,
        )
        if customer_account:
            customer_account.account = receivable_eur
        else:
            customer_doc.append(
                "accounts",
                {"company": company.name, "account": receivable_eur},
            )
        customer_doc.save(ignore_permissions=True)
        item_code = _ensure_item()
        receipt = frappe.get_doc(
            {
                "doctype": "CC Receipt",
                "posting_date": nowdate(),
                "posting_time": "00:01:00",
                "contract": contract.name,
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 1,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "accounting_unit_value": 100,
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        receipt.submit()
        receipt.reload()
        lot = receipt.items[0].stock_lot
        lot_doc = frappe.get_doc("CC Stock Lot", lot)
        off_balance_entry = frappe.get_doc(
            "UA Off Balance Entry",
            receipt.items[0].off_balance_entry,
        )
        self.assertEqual(lot_doc.off_balance_currency, company.default_currency)
        self.assertEqual(off_balance_entry.currency, company.default_currency)
        frappe.db.commit()

        allocation = reserve_stock(
            ReservationRequest(
                idempotency_key="_CC-FX-RESERVATION",
                item_code=item_code,
                company=company.name,
                location=LOCATION,
                qty=Decimal("1"),
                allowed_warehouses=frozenset({warehouses["COMMISSION"]}),
            )
        )
        frappe.db.commit()
        invoice = create_sales_invoice_from_allocations(
            ManagedSaleRequest(
                idempotency_key="_CC-FX-INVOICE",
                customer=customer,
                posting_date=nowdate(),
                currency="EUR",
                conversion_rate=Decimal("1.10"),
                lines=(ManagedSaleLine(allocation.name, Decimal("100")),),
            )
        )
        invoice.submit()
        invoice.reload()
        sale = frappe.get_doc(
            "CC Sale Allocation",
            {"sales_invoice": invoice.name},
        )
        self.assertEqual(sale.currency, "EUR")
        self.assertEqual(Decimal(str(sale.partner_amount)), Decimal("85.0"))
        self.assertEqual(Decimal(str(sale.base_net_amount)), Decimal("110.0"))
        self.assertEqual(Decimal(str(sale.base_commission_amount)), Decimal("16.5"))
        self.assertEqual(Decimal(str(sale.base_partner_amount)), Decimal("93.5"))

        frappe.db.commit()
        report = create_settlement_report(
            SettlementRequest(
                idempotency_key="_CC-FX-SETTLEMENT",
                sale_allocations=(sale.name,),
                period_from=getdate(nowdate()),
                period_to=getdate(nowdate()),
                posting_date=getdate(nowdate()),
            )
        )
        self.assertEqual(report.currency, "EUR")
        self.assertEqual(Decimal(str(report.total_partner_amount)), Decimal("85.0"))
        self.assertEqual(Decimal(str(report.base_total_partner_amount)), Decimal("93.5"))
        self.assertEqual(Decimal(str(report.conversion_rate)), Decimal("1.1"))
        report = submit_settlement_report(report.name)
        debt = frappe.get_doc("Journal Entry", report.debt_journal_entry)
        self.assertTrue(debt.multi_currency)
        source_row = next(
            row
            for row in debt.accounts
            if row.account == mapping.unreported_commission_liability_account
        )
        payable_row = next(row for row in debt.accounts if row.account == payable_eur)
        self.assertEqual(Decimal(str(source_row.debit_in_account_currency)), Decimal("93.5"))
        self.assertEqual(Decimal(str(payable_row.credit_in_account_currency)), Decimal("85.0"))
        self.assertEqual(Decimal(str(payable_row.exchange_rate)), Decimal("1.1"))

        payments = []
        for sequence, amount, rate in (
            (1, Decimal("40"), Decimal("1.20")),
            (2, Decimal("45"), Decimal("1.30")),
        ):
            frappe.db.commit()
            payment = create_settlement_payment(
                SettlementPaymentRequest(
                    idempotency_key=f"_CC-FX-PAYMENT-{sequence}",
                    settlement_report=report.name,
                    bank_account=settlement_bank,
                    amount=amount,
                    posting_date=getdate(nowdate()),
                    reference_no=f"_CC-FX-WIRE-{sequence}",
                    exchange_rate=rate,
                )
            )
            self.assertEqual(Decimal(str(payment.paid_amount)), amount * rate)
            self.assertEqual(Decimal(str(payment.received_amount)), amount)
            payment = submit_settlement_payment(payment.name)
            payments.append(payment)
        report.reload()
        self.assertEqual(report.status, "PAID")
        self.assertEqual(Decimal(str(report.paid_amount)), Decimal("85.0"))
        self.assertEqual(Decimal(str(report.outstanding_amount)), Decimal("0.0"))
        for payment in payments:
            gl = frappe.get_all(
                "GL Entry",
                filters={"voucher_no": payment.name, "is_cancelled": 0},
                fields=["debit", "credit"],
            )
            self.assertEqual(
                sum((Decimal(str(row.debit)) for row in gl), Decimal("0")),
                sum((Decimal(str(row.credit)) for row in gl), Decimal("0")),
            )
        integrity = audit_financial_integrity(company.name)
        self.assertTrue(integrity["ok"], integrity["issues"])

        for payment in reversed(payments):
            payment.cancel()
        cancel_settlement_report(report.name)
        frappe.db.commit()
        returned = create_return_invoice(
            ManagedReturnRequest(
                idempotency_key="_CC-FX-RETURN",
                posting_date=getdate(nowdate()),
                lines=(ManagedReturnLine(sale.name, Decimal("1")),),
            )
        )
        returned.submit()
        audit = frappe.get_doc(
            "CC Sale Return Allocation",
            {"return_sales_invoice": returned.name},
        )
        self.assertEqual(Decimal(str(audit.partner_amount)), Decimal("85.0"))
        self.assertEqual(Decimal(str(audit.base_partner_amount)), Decimal("93.5"))
        returned.cancel()
        invoice.cancel()
        self.assertEqual(get_ownership_balance(lot), Decimal("1"))

    def test_one_sku_uses_all_four_sources_in_global_fifo_order(self) -> None:
        _cleanup_managed_sales()
        _cleanup_own_receipts()
        _cleanup_price_versions()
        _cleanup_receipt_records()
        _cleanup_integration_records()
        self.addCleanup(_cleanup_integration_records)
        self.addCleanup(_cleanup_receipt_records)
        self.addCleanup(_cleanup_price_versions)
        self.addCleanup(_cleanup_own_receipts)
        self.addCleanup(_cleanup_managed_sales)

        company, warehouses, commission_contract = _ensure_receipt_context()
        _ensure_account_mapping(company)
        settings = frappe.get_single("CC Settings")
        settings.enable_buyout = 1
        settings.enable_deferred_purchase = 1
        settings.enable_commission = 1
        settings.enable_consignment = 1
        settings.save(ignore_permissions=True)
        customer = _ensure_customer()
        item_code = _ensure_item()
        supplier = frappe.db.get_value(
            "CC Partner Profile",
            {"partner_name": "_CC Integration Partner"},
            "supplier",
        )
        consignment_contract = frappe.get_doc(
            {
                "doctype": "CC Contract",
                "contract_title": "_CC Four Source Consignment Contract",
                "status": "ACTIVE",
                "partner_profile": PARTNER,
                "company": COMPANY,
                "location": LOCATION,
                "relationship_model": "CONSIGNMENT",
                "currency": company.default_currency,
                "commission_rate": 0,
                "valid_from": nowdate(),
                "settlement_frequency": "MONTHLY",
                "settlement_deadline_days": 7,
                "fiscal_policy": "AUTO",
                "price_authority": "CONTRACT",
            }
        ).insert(ignore_permissions=True)

        own_receipts = []
        for source_method, posting_time, rate in (
            ("BUYOUT", "00:01:00", 40),
            ("DEFERRED_PURCHASE", "00:02:00", 50),
        ):
            values = {
                "doctype": "CC Own Receipt",
                "source_method": source_method,
                "posting_date": nowdate(),
                "posting_time": posting_time,
                "supplier": supplier,
                "company": company.name,
                "location": LOCATION,
                "currency": company.default_currency,
                "conversion_rate": 1,
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 1,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "rate": rate,
                    }
                ],
            }
            if source_method == "DEFERRED_PURCHASE":
                values["due_date"] = add_days(nowdate(), 30)
            receipt = frappe.get_doc(values).insert(ignore_permissions=True)
            receipt.submit()
            receipt.reload()
            own_receipts.append(receipt)

        third_party_receipts = []
        for contract, posting_time in (
            (commission_contract, "00:03:00"),
            (consignment_contract.name, "00:04:00"),
        ):
            receipt = frappe.get_doc(
                {
                    "doctype": "CC Receipt",
                    "posting_date": nowdate(),
                    "posting_time": posting_time,
                    "contract": contract,
                    "items": [
                        {
                            "item_code": item_code,
                            "qty": 1,
                            "uom": "Nos",
                            "conversion_factor": 1,
                            "accounting_unit_value": 100,
                        }
                    ],
                }
            ).insert(ignore_permissions=True)
            receipt.submit()
            receipt.reload()
            third_party_receipts.append(receipt)

        lots = [
            *(receipt.items[0].stock_lot for receipt in own_receipts),
            *(receipt.items[0].stock_lot for receipt in third_party_receipts),
        ]
        consignment_lot = frappe.get_doc("CC Stock Lot", lots[-1])
        price = frappe.get_doc(
            {
                "doctype": "CC Price Version",
                "stock_lot": consignment_lot.name,
                "partner_rate": 70,
                "valid_from": get_datetime(consignment_lot.received_datetime),
                "notes": "Four-source FIFO acceptance price",
            }
        ).insert(ignore_permissions=True)
        price.submit()
        frappe.db.commit()

        expected_methods = [
            "BUYOUT",
            "DEFERRED_PURCHASE",
            "COMMISSION",
            "CONSIGNMENT",
        ]
        fifo_rows = get_fifo_inventory(
            {
                "company": company.name,
                "location": LOCATION,
                "item_code": item_code,
                "available_only": 1,
            }
        )
        self.assertEqual([row["source_method"] for row in fifo_rows], expected_methods)
        self.assertEqual([row["fifo_position"] for row in fifo_rows], [1, 2, 3, 4])
        self.assertEqual(
            [Decimal(str(row["available_qty"])) for row in fifo_rows],
            [Decimal("1.0")] * 4,
        )

        allocation = reserve_stock(
            ReservationRequest(
                idempotency_key="_CC-FOUR-SOURCE-RESERVATION",
                item_code=item_code,
                company=company.name,
                location=LOCATION,
                qty=Decimal("4"),
                allowed_warehouses=frozenset(warehouses.values()),
            )
        )
        self.assertEqual([row.stock_lot for row in allocation.slices], lots)
        self.assertEqual([row.source_method for row in allocation.slices], expected_methods)
        frappe.db.commit()

        invoice = create_sales_invoice_from_allocations(
            ManagedSaleRequest(
                idempotency_key="_CC-FOUR-SOURCE-INVOICE",
                customer=customer,
                posting_date=nowdate(),
                lines=(ManagedSaleLine(allocation.name, Decimal("100")),),
            )
        )
        invoice.submit()
        invoice.reload()
        sold = [
            frappe.get_doc(
                "CC Sale Allocation",
                {"sales_invoice_item": row.name},
            )
            for row in invoice.items
        ]
        self.assertEqual([row.source_method for row in sold], expected_methods)
        self.assertEqual([row.stock_lot for row in sold], lots)
        self.assertEqual(
            [
                (
                    Decimal(str(row.partner_amount)),
                    Decimal(str(row.retained_amount)),
                )
                for row in sold
            ],
            [
                (Decimal("0.0"), Decimal("100.0")),
                (Decimal("0.0"), Decimal("100.0")),
                (Decimal("85.0"), Decimal("15.0")),
                (Decimal("70.0"), Decimal("30.0")),
            ],
        )
        self.assertEqual(
            sum((Decimal(str(row.partner_amount)) for row in sold), Decimal("0")),
            Decimal("155.0"),
        )
        ledger_lots = frappe.get_all(
            "Stock Ledger Entry",
            filters={
                "voucher_type": "Sales Invoice",
                "voucher_no": invoice.name,
                "is_cancelled": 0,
            },
            fields=[OWNERSHIP_FIELD, "actual_qty"],
            order_by="voucher_detail_no asc",
        )
        self.assertEqual({row.get(OWNERSHIP_FIELD) for row in ledger_lots}, set(lots))
        self.assertEqual(
            sum((Decimal(str(row.actual_qty)) for row in ledger_lots), Decimal("0")),
            Decimal("-4.0"),
        )
        integrity = audit_financial_integrity(company.name)
        self.assertTrue(integrity["ok"], integrity["issues"])
        financial_rows = get_sale_financials(
            {"company": company.name, "sales_invoice": invoice.name}
        )
        self.assertEqual([row.source_method for row in financial_rows], expected_methods)
        self.assertEqual(
            sum(
                (Decimal(str(row.retained_after_returns)) for row in financial_rows),
                Decimal("0"),
            ),
            Decimal("245.0"),
        )
        self.assertEqual(
            sum(
                (Decimal(str(row.partner_after_returns)) for row in financial_rows),
                Decimal("0"),
            ),
            Decimal("155.0"),
        )

        invoice.cancel()
        self.assertTrue(all(get_ownership_balance(lot) == 1 for lot in lots))

    def test_managed_invoice_consumes_exact_fifo_and_cancel_restores_stock(self) -> None:
        _cleanup_managed_sales()
        _cleanup_own_receipts()
        _cleanup_receipt_records()
        _cleanup_integration_records()
        self.addCleanup(_cleanup_integration_records)
        self.addCleanup(_cleanup_receipt_records)
        self.addCleanup(_cleanup_own_receipts)
        self.addCleanup(_cleanup_managed_sales)

        company, warehouses, _contract = _ensure_receipt_context()
        settings = frappe.get_single("CC Settings")
        settings.enable_buyout = 1
        settings.save(ignore_permissions=True)
        customer = _ensure_customer()
        item_code = _ensure_item()
        supplier = frappe.db.get_value(
            "CC Partner Profile",
            {"partner_name": "_CC Integration Partner"},
            "supplier",
        )
        receipt = frappe.get_doc(
            {
                "doctype": "CC Own Receipt",
                "source_method": "BUYOUT",
                "posting_date": nowdate(),
                "posting_time": "00:01:00",
                "supplier": supplier,
                "company": company.name,
                "location": LOCATION,
                "currency": company.default_currency,
                "conversion_rate": 1,
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 2,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "rate": 40,
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        receipt.submit()
        receipt.reload()
        lot = frappe.get_doc("CC Stock Lot", receipt.items[0].stock_lot)

        unmanaged = _unmanaged_invoice(company, customer, item_code, warehouses["OWN"])
        with self.assertRaisesRegex(frappe.ValidationError, "managed FIFO allocation"):
            unmanaged.submit()
        frappe.delete_doc("Sales Invoice", unmanaged.name, force=True, ignore_permissions=True)
        frappe.db.commit()

        allocation = reserve_stock(
            ReservationRequest(
                idempotency_key="_CC-SALE-RESERVATION-1",
                item_code=item_code,
                company=company.name,
                location=LOCATION,
                qty=Decimal("1"),
                allowed_warehouses=frozenset({warehouses["OWN"]}),
            )
        )
        frappe.db.commit()
        sale_request = ManagedSaleRequest(
            idempotency_key="_CC-SALE-INVOICE-1",
            customer=customer,
            posting_date=nowdate(),
            lines=(ManagedSaleLine(allocation.name, Decimal("100")),),
        )
        invoice = create_sales_invoice_from_allocations(sale_request)
        self.assertTrue(invoice.get(MANAGED_SALE_FIELD))
        self.assertEqual(invoice.items[0].get(ALLOCATION_FIELD), allocation.name)
        self.assertEqual(invoice.items[0].get(OWNERSHIP_FIELD), lot.name)
        frappe.db.commit()

        replay = create_sales_invoice_from_allocations(sale_request)
        self.assertEqual(replay.name, invoice.name)
        invoice.items[0].set(OWNERSHIP_FIELD, None)
        with self.assertRaisesRegex(frappe.ValidationError, "Allocation Slice changed"):
            invoice.submit()
        invoice.reload()
        invoice.submit()
        invoice.reload()
        allocation.reload()
        lot.reload()
        self.assertEqual(invoice.docstatus, 1)
        self.assertEqual(allocation.status, "CONSUMED")
        self.assertEqual(allocation.consumer_document, invoice.name)
        self.assertEqual(Decimal(str(lot.reserved_qty)), Decimal("0.0"))
        self.assertEqual(get_ownership_balance(lot.name), Decimal("1"))

        sold = frappe.get_doc(
            "CC Sale Allocation",
            {"sales_invoice_item": invoice.items[0].name},
        )
        self.assertEqual(sold.status, "SOLD")
        self.assertEqual(sold.sales_invoice, invoice.name)
        self.assertEqual(sold.stock_lot, lot.name)
        self.assertEqual(sold.relationship_model, "OWN")
        self.assertEqual(Decimal(str(sold.sold_qty)), Decimal("1.0"))
        self.assertEqual(Decimal(str(sold.net_amount)), Decimal("100.0"))
        self.assertEqual(Decimal(str(sold.partner_amount)), Decimal("0.0"))
        self.assertEqual(Decimal(str(sold.retained_amount)), Decimal("100.0"))
        self.assertFalse(sold.recognition_journal_entry)

        ledger = frappe.get_all(
            "Stock Ledger Entry",
            filters={
                "voucher_type": "Sales Invoice",
                "voucher_no": invoice.name,
                "is_cancelled": 0,
            },
            fields=["actual_qty", OWNERSHIP_FIELD, "warehouse"],
        )
        self.assertEqual(len(ledger), 1)
        self.assertEqual(Decimal(str(ledger[0].actual_qty)), Decimal("-1.0"))
        self.assertEqual(ledger[0].get(OWNERSHIP_FIELD), lot.name)
        self.assertEqual(ledger[0].warehouse, warehouses["OWN"])

        invoice.cancel()
        sold.reload()
        self.assertEqual(sold.status, "CANCELLED")
        self.assertEqual(get_ownership_balance(lot.name), Decimal("2"))
        frappe.db.commit()
        reusable = reserve_stock(
            ReservationRequest(
                idempotency_key="_CC-SALE-RESERVATION-2",
                item_code=item_code,
                company=company.name,
                location=LOCATION,
                qty=Decimal("1"),
                allowed_warehouses=frozenset({warehouses["OWN"]}),
            )
        )
        self.assertEqual(reusable.slices[0].stock_lot, lot.name)
        release_allocation(reusable.name, reason="Integration sale cancellation proof complete")

    def test_commission_and_consignment_income_and_partner_debt_are_exact(self) -> None:
        _cleanup_managed_sales()
        _cleanup_price_versions()
        _cleanup_receipt_records()
        _cleanup_integration_records()
        self.addCleanup(_cleanup_integration_records)
        self.addCleanup(_cleanup_receipt_records)
        self.addCleanup(_cleanup_price_versions)
        self.addCleanup(_cleanup_managed_sales)

        company, warehouses, commission_contract = _ensure_receipt_context()
        mapping, _accounts = _ensure_account_mapping(company)
        from erpnext_consignment_and_commission.consignment_and_commission.spikes.accounting import (
            _ensure_account,
        )

        settlement_bank = _ensure_account(
            frappe,
            company=company.name,
            account_name="_CC Integration Settlement Bank",
            parent_name="Bank Accounts",
            currency=company.default_currency,
            account_type="Bank",
        )
        customer = _ensure_customer()
        item_code = _ensure_item()
        consignment_contract = frappe.get_doc(
            {
                "doctype": "CC Contract",
                "contract_title": "_CC Integration Sale Consignment Contract",
                "status": "ACTIVE",
                "partner_profile": PARTNER,
                "company": COMPANY,
                "location": LOCATION,
                "relationship_model": "CONSIGNMENT",
                "currency": company.default_currency,
                "commission_rate": 0,
                "valid_from": nowdate(),
                "settlement_frequency": "MONTHLY",
                "settlement_deadline_days": 7,
                "fiscal_policy": "AUTO",
                "price_authority": "CONTRACT",
            }
        ).insert(ignore_permissions=True)

        receipts = []
        for contract, posting_time in (
            (commission_contract, "00:01:00"),
            (consignment_contract.name, "00:02:00"),
        ):
            receipt = frappe.get_doc(
                {
                    "doctype": "CC Receipt",
                    "posting_date": nowdate(),
                    "posting_time": posting_time,
                    "contract": contract,
                    "items": [
                        {
                            "item_code": item_code,
                            "qty": 1,
                            "uom": "Nos",
                            "conversion_factor": 1,
                            "accounting_unit_value": 100,
                        }
                    ],
                }
            ).insert(ignore_permissions=True)
            receipt.submit()
            receipt.reload()
            receipts.append(receipt)

        commission_lot = frappe.get_doc("CC Stock Lot", receipts[0].items[0].stock_lot)
        consignment_lot = frappe.get_doc("CC Stock Lot", receipts[1].items[0].stock_lot)
        price = frappe.get_doc(
            {
                "doctype": "CC Price Version",
                "stock_lot": consignment_lot.name,
                "partner_rate": 70,
                "valid_from": get_datetime(consignment_lot.received_datetime),
                "notes": "Integration sale partner-price evidence",
            }
        ).insert(ignore_permissions=True)
        price.submit()
        frappe.db.commit()

        allocation = reserve_stock(
            ReservationRequest(
                idempotency_key="_CC-SALE-THIRD-PARTY-RESERVATION-1",
                item_code=item_code,
                company=company.name,
                location=LOCATION,
                qty=Decimal("2"),
                allowed_warehouses=frozenset(
                    {warehouses["COMMISSION"], warehouses["CONSIGNMENT"]}
                ),
            )
        )
        self.assertEqual(
            [row.stock_lot for row in allocation.slices],
            [commission_lot.name, consignment_lot.name],
        )
        self.assertEqual(
            [row.relationship_model for row in allocation.slices],
            ["COMMISSION", "CONSIGNMENT"],
        )
        frappe.db.commit()

        invoice = create_sales_invoice_from_allocations(
            ManagedSaleRequest(
                idempotency_key="_CC-SALE-THIRD-PARTY-INVOICE-1",
                customer=customer,
                posting_date=nowdate(),
                lines=(ManagedSaleLine(allocation.name, Decimal("100")),),
            )
        )
        self.assertEqual(
            [row.income_account for row in invoice.items],
            [mapping.gross_proceeds_clearing_account, mapping.gross_proceeds_clearing_account],
        )
        invoice.submit()
        invoice.reload()
        allocation.reload()
        self.assertEqual(allocation.status, "CONSUMED")

        snapshots = {
            row.relationship_model: row
            for row in frappe.get_all(
                "CC Sale Allocation",
                filters={"sales_invoice": invoice.name},
                fields=["*"],
            )
        }
        self.assertEqual(set(snapshots), {"COMMISSION", "CONSIGNMENT"})
        commission = snapshots["COMMISSION"]
        self.assertEqual(Decimal(str(commission.net_amount)), Decimal("100.0"))
        self.assertEqual(Decimal(str(commission.commission_rate)), Decimal("15.0"))
        self.assertEqual(Decimal(str(commission.commission_amount)), Decimal("15.0"))
        self.assertEqual(Decimal(str(commission.partner_amount)), Decimal("85.0"))
        self.assertEqual(Decimal(str(commission.retained_amount)), Decimal("15.0"))
        consignment = snapshots["CONSIGNMENT"]
        self.assertEqual(consignment.price_version, price.name)
        self.assertEqual(Decimal(str(consignment.partner_unit_rate)), Decimal("70.0"))
        self.assertEqual(Decimal(str(consignment.partner_amount)), Decimal("70.0"))
        self.assertEqual(Decimal(str(consignment.retained_amount)), Decimal("30.0"))
        self.assertEqual(
            sum(Decimal(str(row.partner_amount)) for row in snapshots.values()),
            Decimal("155.0"),
        )
        self.assertEqual(
            sum(Decimal(str(row.retained_amount)) for row in snapshots.values()),
            Decimal("45.0"),
        )
        self.assertEqual(
            {row.relationship_model: Decimal(str(row.off_balance_amount)) for row in snapshots.values()},
            {"COMMISSION": Decimal("100.0"), "CONSIGNMENT": Decimal("100.0")},
        )
        self.assertTrue(all(row.off_balance_entry for row in snapshots.values()))
        sale_024 = frappe.get_all(
            "UA Off Balance Entry",
            filters={
                "reference_doctype": "Sales Invoice",
                "reference_name": invoice.name,
                "docstatus": 1,
            },
            fields=["direction", "quantity", "amount"],
        )
        self.assertEqual(len(sale_024), 2)
        self.assertEqual({row.direction for row in sale_024}, {"Decrease"})
        self.assertEqual(
            sum(Decimal(str(row.quantity)) for row in sale_024),
            Decimal("2.0"),
        )
        self.assertEqual(
            sum(Decimal(str(row.amount)) for row in sale_024),
            Decimal("200.0"),
        )

        journal_name = frappe.db.get_value(
            "Journal Entry",
            {
                SALES_INVOICE_FIELD: invoice.name,
                POSTING_KIND_FIELD: "SALE_RECOGNITION",
            },
            "name",
        )
        self.assertTrue(journal_name)
        journal = frappe.get_doc("Journal Entry", journal_name)
        self.assertEqual(journal.docstatus, 1)
        journal_lines = {
            row.account: (
                Decimal(str(row.debit_in_account_currency or 0)),
                Decimal(str(row.credit_in_account_currency or 0)),
            )
            for row in journal.accounts
        }
        self.assertEqual(
            journal_lines,
            {
                mapping.gross_proceeds_clearing_account: (
                    Decimal("45.0"),
                    Decimal("0.0"),
                ),
                mapping.commission_revenue_account: (
                    Decimal("0.0"),
                    Decimal("45.0"),
                ),
                mapping.principal_proceeds_deduction_account: (
                    Decimal("155.0"),
                    Decimal("0.0"),
                ),
                mapping.unreported_commission_liability_account: (
                    Decimal("0.0"),
                    Decimal("85.0"),
                ),
                mapping.unreported_consignment_liability_account: (
                    Decimal("0.0"),
                    Decimal("70.0"),
                ),
            },
        )
        self.assertEqual(
            sum((line[0] for line in journal_lines.values()), Decimal("0")),
            Decimal("200.0"),
        )
        self.assertEqual(
            sum((line[1] for line in journal_lines.values()), Decimal("0")),
            Decimal("200.0"),
        )

        reports = {}
        for relationship_model in ("COMMISSION", "CONSIGNMENT"):
            frappe.db.commit()
            snapshot = snapshots[relationship_model]
            report = create_settlement_report(
                SettlementRequest(
                    idempotency_key=f"_CC-SETTLEMENT-{relationship_model}-1",
                    sale_allocations=(snapshot.name,),
                    period_from=getdate(nowdate()),
                    period_to=getdate(nowdate()),
                    posting_date=getdate(nowdate()),
                )
            )
            self.assertEqual(report.docstatus, 0)
            self.assertEqual(
                Decimal(str(report.total_partner_amount)),
                Decimal(str(snapshot.partner_amount)),
            )
            self.assertEqual(
                frappe.db.get_value(
                    "CC Sale Allocation",
                    snapshot.name,
                    "settlement_report",
                ),
                report.name,
            )
            report = submit_settlement_report(report.name)
            report.reload()
            self.assertEqual(report.docstatus, 1)
            self.assertEqual(report.status, "PAYABLE")
            self.assertEqual(
                Decimal(str(report.outstanding_amount)),
                Decimal(str(snapshot.partner_amount)),
            )
            debt = frappe.get_doc("Journal Entry", report.debt_journal_entry)
            self.assertEqual(debt.docstatus, 1)
            debt_lines = {
                row.account: (
                    Decimal(str(row.debit_in_account_currency or 0)),
                    Decimal(str(row.credit_in_account_currency or 0)),
                    row.party_type,
                    row.party,
                )
                for row in debt.accounts
            }
            source_account = {
                "COMMISSION": mapping.unreported_commission_liability_account,
                "CONSIGNMENT": mapping.unreported_consignment_liability_account,
            }[relationship_model]
            amount = Decimal(str(snapshot.partner_amount))
            self.assertEqual(
                debt_lines,
                {
                    source_account: (amount, Decimal("0.0"), None, None),
                    mapping.default_supplier_payable_account: (
                        Decimal("0.0"),
                        amount,
                        "Supplier",
                        snapshot.supplier,
                    ),
                },
            )
            with self.assertRaisesRegex(
                frappe.ValidationError,
                "Cancel CC Settlement Report",
            ):
                debt.cancel()
            reports[relationship_model] = report

        commission_report = reports["COMMISSION"]
        frappe.db.commit()
        first_payment_request = SettlementPaymentRequest(
            idempotency_key="_CC-SETTLEMENT-PAYMENT-1",
            settlement_report=commission_report.name,
            bank_account=settlement_bank,
            amount=Decimal("25"),
            posting_date=getdate(nowdate()),
            reference_no="_CC-WIRE-1",
        )
        first_payment = create_settlement_payment(first_payment_request)
        self.assertEqual(first_payment.docstatus, 0)
        self.assertEqual(first_payment.references[0].reference_name, commission_report.debt_journal_entry)
        frappe.db.commit()
        replay_payment = create_settlement_payment(first_payment_request)
        self.assertEqual(replay_payment.name, first_payment.name)
        first_payment = submit_settlement_payment(first_payment.name)
        commission_report.reload()
        self.assertEqual(commission_report.status, "PARTIALLY_PAID")
        self.assertEqual(Decimal(str(commission_report.paid_amount)), Decimal("25.0"))
        self.assertEqual(Decimal(str(commission_report.outstanding_amount)), Decimal("60.0"))

        frappe.db.commit()
        second_payment = create_settlement_payment(
            SettlementPaymentRequest(
                idempotency_key="_CC-SETTLEMENT-PAYMENT-2",
                settlement_report=commission_report.name,
                bank_account=settlement_bank,
                amount=Decimal("60"),
                posting_date=getdate(nowdate()),
                reference_no="_CC-WIRE-2",
            )
        )
        second_payment = submit_settlement_payment(second_payment.name)
        commission_report.reload()
        self.assertEqual(commission_report.status, "PAID")
        self.assertEqual(Decimal(str(commission_report.paid_amount)), Decimal("85.0"))
        self.assertEqual(Decimal(str(commission_report.outstanding_amount)), Decimal("0.0"))
        integrity = audit_financial_integrity(company.name)
        self.assertTrue(integrity["ok"], integrity["issues"])
        frappe.db.commit()
        with self.assertRaisesRegex(SettlementPaymentError, "not payable"):
            create_settlement_payment(
                SettlementPaymentRequest(
                    idempotency_key="_CC-SETTLEMENT-OVERPAYMENT",
                    settlement_report=commission_report.name,
                    bank_account=settlement_bank,
                    amount=Decimal("1"),
                    posting_date=getdate(nowdate()),
                    reference_no="_CC-WIRE-OVERPAYMENT",
                )
            )

        frappe.db.commit()
        reported_return = create_return_invoice(
            ManagedReturnRequest(
                idempotency_key="_CC-REPORTED-RETURN-1",
                posting_date=getdate(nowdate()),
                lines=(
                    ManagedReturnLine(snapshots["COMMISSION"].name, Decimal("1")),
                    ManagedReturnLine(snapshots["CONSIGNMENT"].name, Decimal("1")),
                ),
            )
        )
        reported_return.submit()
        commission_report.reload()
        reports["CONSIGNMENT"].reload()
        self.assertEqual(commission_report.status, "CREDIT_DUE")
        self.assertEqual(Decimal(str(commission_report.adjusted_amount)), Decimal("85.0"))
        self.assertEqual(Decimal(str(commission_report.net_partner_amount)), Decimal("0.0"))
        self.assertEqual(Decimal(str(commission_report.partner_credit_amount)), Decimal("85.0"))
        consignment_report = reports["CONSIGNMENT"]
        self.assertEqual(consignment_report.status, "ADJUSTED")
        self.assertEqual(Decimal(str(consignment_report.adjusted_amount)), Decimal("70.0"))
        self.assertEqual(Decimal(str(consignment_report.outstanding_amount)), Decimal("0.0"))
        adjustments = frappe.get_all(
            "CC Settlement Adjustment",
            filters={"return_sales_invoice": reported_return.name, "docstatus": 1},
            fields=["*"],
        )
        self.assertEqual(len(adjustments), 2)
        for adjustment in adjustments:
            adjustment_journal = frappe.get_doc("Journal Entry", adjustment.journal_entry)
            self.assertEqual(adjustment_journal.docstatus, 1)
            payable_row = next(
                row
                for row in adjustment_journal.accounts
                if row.party_type == "Supplier"
            )
            if adjustment.settlement_report == consignment_report.name:
                self.assertEqual(payable_row.reference_type, "Journal Entry")
                self.assertEqual(payable_row.reference_name, consignment_report.debt_journal_entry)
            else:
                self.assertFalse(payable_row.reference_name)
                self.assertEqual(payable_row.is_advance, "Yes")
            with self.assertRaisesRegex(
                frappe.ValidationError,
                "Cancel CC Settlement Adjustment",
            ):
                adjustment_journal.cancel()
        integrity = audit_financial_integrity(company.name)
        self.assertTrue(integrity["ok"], integrity["issues"])
        with self.assertRaisesRegex(frappe.ValidationError, "Settlement Adjustments"):
            cancel_settlement_report(consignment_report.name)

        reported_return.cancel()
        commission_report.reload()
        consignment_report.reload()
        self.assertEqual(commission_report.status, "PAID")
        self.assertEqual(Decimal(str(commission_report.adjusted_amount)), Decimal("0.0"))
        self.assertEqual(Decimal(str(commission_report.partner_credit_amount)), Decimal("0.0"))
        self.assertEqual(consignment_report.status, "PAYABLE")
        self.assertEqual(Decimal(str(consignment_report.outstanding_amount)), Decimal("70.0"))
        with self.assertRaisesRegex(frappe.ValidationError, "Cancel linked payments"):
            cancel_settlement_report(commission_report.name)
        commission_report.reload()
        self.assertEqual(commission_report.docstatus, 1)

        second_payment.cancel()
        commission_report.reload()
        self.assertEqual(commission_report.status, "PARTIALLY_PAID")
        self.assertEqual(Decimal(str(commission_report.outstanding_amount)), Decimal("60.0"))
        first_payment.cancel()
        commission_report.reload()
        self.assertEqual(commission_report.status, "PAYABLE")
        self.assertEqual(Decimal(str(commission_report.paid_amount)), Decimal("0.0"))
        self.assertEqual(Decimal(str(commission_report.outstanding_amount)), Decimal("85.0"))

        with self.assertRaisesRegex(frappe.ValidationError, "linked Settlement Reports"):
            invoice.cancel()
        invoice.reload()
        self.assertEqual(invoice.docstatus, 1)
        for report in reversed(list(reports.values())):
            cancel_settlement_report(report.name)
            report.reload()
            self.assertEqual(report.docstatus, 2)
            self.assertEqual(report.status, "CANCELLED")

        frappe.db.commit()
        return_invoice = create_return_invoice(
            ManagedReturnRequest(
                idempotency_key="_CC-MANAGED-RETURN-1",
                posting_date=getdate(nowdate()),
                lines=(
                    ManagedReturnLine(snapshots["COMMISSION"].name, Decimal("1")),
                    ManagedReturnLine(snapshots["CONSIGNMENT"].name, Decimal("1")),
                ),
            )
        )
        self.assertTrue(return_invoice.is_return)
        self.assertEqual(return_invoice.return_against, invoice.name)
        self.assertEqual(
            [row.get(OWNERSHIP_FIELD) for row in return_invoice.items],
            [commission_lot.name, consignment_lot.name],
        )
        return_invoice.submit()
        return_invoice.reload()
        self.assertEqual(return_invoice.docstatus, 1)
        return_audits = frappe.get_all(
            "CC Sale Return Allocation",
            filters={"return_sales_invoice": return_invoice.name},
            fields=["*"],
        )
        self.assertEqual(len(return_audits), 2)
        self.assertEqual({row.status for row in return_audits}, {"RETURNED"})
        self.assertEqual(
            {row.relationship_model: Decimal(str(row.partner_amount)) for row in return_audits},
            {"COMMISSION": Decimal("85.0"), "CONSIGNMENT": Decimal("70.0")},
        )
        self.assertEqual(
            {row.relationship_model: Decimal(str(row.off_balance_amount)) for row in return_audits},
            {"COMMISSION": Decimal("100.0"), "CONSIGNMENT": Decimal("100.0")},
        )
        return_024 = frappe.get_all(
            "UA Off Balance Entry",
            filters={
                "reference_doctype": "Sales Invoice",
                "reference_name": return_invoice.name,
                "docstatus": 1,
            },
            fields=["direction", "quantity", "amount"],
        )
        self.assertEqual(len(return_024), 2)
        self.assertEqual({row.direction for row in return_024}, {"Increase"})
        self.assertEqual(
            sum(Decimal(str(row.quantity)) for row in return_024),
            Decimal("2.0"),
        )
        self.assertEqual(
            sum(Decimal(str(row.amount)) for row in return_024),
            Decimal("200.0"),
        )
        self.assertEqual(
            set(
                frappe.get_all(
                    "CC Sale Allocation",
                    filters={"sales_invoice": invoice.name},
                    pluck="status",
                )
            ),
            {"RETURNED"},
        )
        reversal_name = frappe.db.get_value(
            "Journal Entry",
            {
                SALES_INVOICE_FIELD: return_invoice.name,
                POSTING_KIND_FIELD: "RETURN_REVERSAL",
            },
            "name",
        )
        reversal = frappe.get_doc("Journal Entry", reversal_name)
        reversal_lines = {
            row.account: (
                Decimal(str(row.debit_in_account_currency or 0)),
                Decimal(str(row.credit_in_account_currency or 0)),
            )
            for row in reversal.accounts
        }
        self.assertEqual(
            reversal_lines,
            {
                mapping.gross_proceeds_clearing_account: (
                    Decimal("0.0"),
                    Decimal("45.0"),
                ),
                mapping.commission_revenue_account: (
                    Decimal("45.0"),
                    Decimal("0.0"),
                ),
                mapping.principal_proceeds_deduction_account: (
                    Decimal("0.0"),
                    Decimal("155.0"),
                ),
                mapping.unreported_commission_liability_account: (
                    Decimal("85.0"),
                    Decimal("0.0"),
                ),
                mapping.unreported_consignment_liability_account: (
                    Decimal("70.0"),
                    Decimal("0.0"),
                ),
            },
        )
        self.assertEqual(get_ownership_balance(commission_lot.name), Decimal("1"))
        self.assertEqual(get_ownership_balance(consignment_lot.name), Decimal("1"))
        return_invoice.cancel()
        reversal.reload()
        self.assertEqual(reversal.docstatus, 2)
        self.assertFalse(
            frappe.db.exists(
                "UA Off Balance Entry",
                {
                    "reference_doctype": "Sales Invoice",
                    "reference_name": return_invoice.name,
                    "docstatus": 1,
                },
            )
        )
        self.assertEqual(get_ownership_balance(commission_lot.name), Decimal("0"))
        self.assertEqual(get_ownership_balance(consignment_lot.name), Decimal("0"))

        with self.assertRaisesRegex(frappe.ValidationError, "Cancel managed Sales Invoice"):
            journal.cancel()
        journal.reload()
        self.assertEqual(journal.docstatus, 1)

        invoice.cancel()
        journal.reload()
        self.assertEqual(journal.docstatus, 2)
        self.assertEqual(
            set(
                frappe.get_all(
                    "CC Sale Allocation",
                    filters={"sales_invoice": invoice.name},
                    pluck="status",
                )
            ),
            {"CANCELLED"},
        )
        self.assertEqual(get_ownership_balance(commission_lot.name), Decimal("1"))
        self.assertEqual(get_ownership_balance(consignment_lot.name), Decimal("1"))
        self.assertFalse(
            frappe.db.exists(
                "UA Off Balance Entry",
                {
                    "reference_doctype": "Sales Invoice",
                    "reference_name": invoice.name,
                    "docstatus": 1,
                },
            )
        )
