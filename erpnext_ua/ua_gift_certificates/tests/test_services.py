import json
from collections import OrderedDict
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_ua.group_stock_fifo.services.checkout import _service_write
from erpnext_ua.group_stock_fifo.services.fulfillment_domain import (
    FulfillmentRouteKey,
    ProviderAllocationRef,
)
from erpnext_ua.group_stock_fifo.services.fulfillment_payments import split_pos_payments
from erpnext_ua.group_stock_fifo.services.fulfillment_reservation import serialize_refs
from erpnext_ua.ua_gift_certificates.adapters.accounting import invoice_payments
from erpnext_ua.ua_gift_certificates.adapters.pos import (
    checkout_payment_rows,
    prepare_return_order,
    return_payment_rows,
)
from erpnext_ua.ua_gift_certificates.adapters.sales_invoice import prepare_invoice
from erpnext_ua.ua_gift_certificates.services.batch import generate_batch
from erpnext_ua.ua_gift_certificates.services.fulfillment import (
    reservation_plan,
    return_route_payment_components,
    sale_route_payment_components,
)
from erpnext_ua.ua_gift_certificates.services.issuance import activate_certificate, issue_certificate
from erpnext_ua.ua_gift_certificates.services.printing import claim_sale_print_payload
from erpnext_ua.ua_gift_certificates.services.reconciliation import reconcile_certificate
from erpnext_ua.ua_gift_certificates.services.redemption import quote_redemption
from erpnext_ua.ua_gift_certificates.services.reservation import reserve_redemption


class TestGiftCertificateServices(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        self.suffix = frappe.generate_hash(length=8)
        self.old_hmac = frappe.conf.get("ua_gift_certificate_hmac_key")
        frappe.conf.ua_gift_certificate_hmac_key = f"gift-certificate-test-{self.suffix}"
        self._base_context()
        self._profiles()
        self._enable_feature()

    def tearDown(self):
        if self.old_hmac is None:
            frappe.conf.pop("ua_gift_certificate_hmac_key", None)
        else:
            frappe.conf.ua_gift_certificate_hmac_key = self.old_hmac
        super().tearDown()

    def test_discounted_certificate_sale_redemption_and_three_partial_returns(self):
        certificate, token = issue_certificate(
            program_name=self.program.name,
            face_value="300",
            sale_price="240",
            issuer_company=self.company,
            issuer_fop_profile=self.fop_profile,
            issuer_cash_desk=self.desk.name,
            idempotency_key=f"test:issue:{self.suffix}",
        )
        sale_reference = self._certificate_sale(certificate)
        activate_certificate(
            certificate.name,
            sale_reference=sale_reference.name,
            payment_evidence=f"test-payment:{self.suffix}",
            idempotency_key=f"test:activate:{self.suffix}",
        )
        certificate.reload()
        self.assertEqual(Decimal(str(certificate.paid_balance)), Decimal("240.00"))
        self.assertEqual(Decimal(str(certificate.promotional_balance)), Decimal("60.00"))

        order = self._sale_order(qty=3, rate=100)
        quote = quote_redemption(order, token, "200")
        reserve_redemption(
            order.name,
            quote["quote_id"],
            idempotency_key=f"test:reserve:{self.suffix}",
        )
        order.reload()
        for payment in checkout_payment_rows(order):
            order.append("payments_plan", {**payment, "status": "Confirmed"})
        order.append("payments_plan", self._cash_payment("100"))
        order.save(ignore_permissions=True)

        invoice = self._submit_sale_invoice(order)
        certificate.reload()
        self.assertEqual(Decimal(str(certificate.current_balance)), Decimal("100.00"))
        self.assertEqual(
            frappe.db.count("UA Gift Certificate Redemption Allocation", {"sales_invoice": invoice.name}),
            1,
        )

        restored = []
        for index in range(3):
            return_order = self._return_order(order, index)
            restored.append(Decimal(str(return_order.gift_certificate_redeemed_total)))
            self._submit_return_invoice(invoice, return_order)
        certificate.reload()

        self.assertEqual(restored, [Decimal("66.67"), Decimal("66.67"), Decimal("66.66")])
        self.assertEqual(Decimal(str(certificate.current_balance)), Decimal("300.00"))
        self.assertEqual(Decimal(str(certificate.paid_balance)), Decimal("240.00"))
        self.assertEqual(Decimal(str(certificate.promotional_balance)), Decimal("60.00"))
        self.assertEqual(reconcile_certificate(certificate.name)["status"], "Clean")

    def test_sales_invoice_payment_posts_liability_and_reverses_on_return(self):
        order = self._sale_order(qty=1, rate=100)
        certificate = self._active_full_value_certificate()
        token = certificate.get_password("token_ciphertext")
        quote = quote_redemption(order, token, "100")
        reserve_redemption(
            order.name,
            quote["quote_id"],
            idempotency_key=f"test:full:reserve:{self.suffix}",
        )
        order.reload()
        for payment in checkout_payment_rows(order):
            order.append("payments_plan", {**payment, "status": "Confirmed"})
        order.save(ignore_permissions=True)
        invoice = self._submit_sale_invoice(order)
        liability_debit = self._gl_total(invoice.name, self.liability_account, "debit")
        self.assertEqual(liability_debit, Decimal("100.00"))

        return_order = self._return_order(order, 0)
        return_invoice = self._submit_return_invoice(invoice, return_order)
        liability_credit = self._gl_total(return_invoice.name, self.liability_account, "credit")
        self.assertEqual(liability_credit, Decimal("100.00"))

    def test_print_grant_exposes_token_once_and_requires_replacement_for_reprint(self):
        certificate = self._active_full_value_certificate()
        sale = frappe.get_doc("UA Gift Certificate Sale", certificate.certificate_sale)
        frappe.db.set_value(
            "POS Order",
            sale.pos_order,
            {
                "order_purpose": "Gift Certificate Sale",
                "gift_certificate_sale": sale.name,
                "status": "Posted",
            },
            update_modified=False,
        )
        payload = claim_sale_print_payload(sale.pos_order, idempotency_key=f"test:print:{self.suffix}")
        self.assertEqual(len(payload["certificates"]), 1)
        self.assertTrue(payload["certificates"][0]["token"].startswith("GC1-"))
        self.assertEqual(
            frappe.db.count("UA Gift Certificate Print Grant", {"certificate": certificate.name}),
            1,
        )
        with self.assertRaisesRegex(Exception, "replacement"):
            claim_sale_print_payload(sale.pos_order, idempotency_key=f"test:reprint:{self.suffix}")

    def test_batch_generation_is_idempotent(self):
        batch = frappe.get_doc(
            {
                "doctype": "UA Gift Certificate Batch",
                "batch_name": f"Test Batch {self.suffix}",
                "program": self.program.name,
                "quantity": 3,
                "face_value": 50,
                "sale_price": 50,
                "holder_mode": "Bearer",
                "activation_trigger": "On Full Payment",
                "idempotency_key": f"test:batch:{self.suffix}",
            }
        ).insert(ignore_permissions=True)
        first = generate_batch(batch.name, commit_progress=False)
        second = generate_batch(batch.name, commit_progress=False)
        self.assertEqual(first["generated_count"], 3)
        self.assertEqual(second["generated_count"], 3)
        self.assertEqual(frappe.db.count("UA Gift Certificate", {"batch": batch.name}), 3)

    def test_two_certificates_and_cash_topup_post_exactly(self):
        first = self._active_certificate("60", "multi-a")
        second = self._active_certificate("60", "multi-b")
        order = self._sale_order(qty=1, rate=150)
        for certificate, requested, label in ((first, "60", "a"), (second, "40", "b")):
            token = certificate.get_password("token_ciphertext")
            quote = quote_redemption(order, token, requested)
            reserve_redemption(
                order.name,
                quote["quote_id"],
                idempotency_key=f"test:multi:{self.suffix}:{label}",
            )
            order.reload()
        for payment in checkout_payment_rows(order):
            order.append("payments_plan", {**payment, "status": "Confirmed"})
        order.append("payments_plan", self._cash_payment("50"))
        order.save(ignore_permissions=True)
        self._submit_sale_invoice(order)
        first.reload()
        second.reload()
        self.assertEqual(Decimal(str(first.current_balance)), Decimal("0.00"))
        self.assertEqual(Decimal(str(second.current_balance)), Decimal("20.00"))

    def test_feature_disabled_keeps_ordinary_payment_adapter_unchanged(self):
        settings = frappe.get_single("UA Gift Certificate Settings")
        settings.enabled = 0
        settings.pos_redemption_enabled = 0
        settings.save(ignore_permissions=True)
        order = self._sale_order(qty=1, rate=25)
        order.append("payments_plan", self._cash_payment("25"))
        order.save(ignore_permissions=True)
        self.assertEqual(
            invoice_payments(order, is_return=False),
            [{"mode_of_payment": self.cash_mode.name, "amount": Decimal("25")}],
        )

    def test_fulfillment_reservation_splits_paid_and_promotional_funding_by_route(self):
        reservation = SimpleNamespace(
            policy_snapshot_json='{"allocations":[{"row":"POS-ROW-1","amount":"30.00"}]}',
            requested_amount="30.00",
            paid_component_reserved="24.00",
        )
        with patch(
            "erpnext_ua.ua_gift_certificates.services.fulfillment.route_quantities",
            return_value=OrderedDict(
                (("ROUTE-A", Decimal("1")), ("ROUTE-B", Decimal("2")))
            ),
        ):
            plan = reservation_plan(SimpleNamespace(), reservation)

        self.assertEqual(
            [
                (row.route_id, row.qty, row.amount, row.paid, row.promotional)
                for row in plan
            ],
            [
                (
                    "ROUTE-A",
                    Decimal("1"),
                    Decimal("10.00"),
                    Decimal("8.00"),
                    Decimal("2.00"),
                ),
                (
                    "ROUTE-B",
                    Decimal("2"),
                    Decimal("20.00"),
                    Decimal("16.00"),
                    Decimal("4.00"),
                ),
            ],
        )

    def test_fulfillment_payments_keep_gift_components_on_their_legal_routes(self):
        order = SimpleNamespace(
            payments_plan=[
                SimpleNamespace(
                    status="Confirmed",
                    kind="Cash",
                    mode_of_payment=self.cash_mode.name,
                    amount="60.00",
                )
            ]
        )
        result = split_pos_payments(
            order,
            OrderedDict(
                (("ROUTE-A", Decimal("40.00")), ("ROUTE-B", Decimal("50.00")))
            ),
            fixed_route_payments={
                "ROUTE-A": [{"mode_of_payment": "Gift A", "amount": Decimal("10.00")}],
                "ROUTE-B": [{"mode_of_payment": "Gift B", "amount": Decimal("20.00")}],
            },
        )

        self.assertEqual(
            result,
            {
                "ROUTE-A": [
                    {"mode_of_payment": "Gift A", "amount": 10.0},
                    {"mode_of_payment": self.cash_mode.name, "amount": 30.0},
                ],
                "ROUTE-B": [
                    {"mode_of_payment": "Gift B", "amount": 20.0},
                    {"mode_of_payment": self.cash_mode.name, "amount": 30.0},
                ],
            },
        )

    def test_fulfillment_posts_and_restores_the_exact_invoice_certificate_slice(self):
        company_group = frappe.db.get_value("GSF Company Group", {}, "name")
        location = frappe.db.get_value(
            "GSF Physical Location", {"company_group": company_group}, "name"
        )
        if not company_group or not location:
            self.skipTest("GSF acceptance fixture is unavailable")

        certificate = self._active_full_value_certificate()
        token = certificate.get_password("token_ciphertext")
        order = self._sale_order(qty=2, rate=100)
        quote = quote_redemption(order, token, "100")
        reservation = reserve_redemption(
            order.name,
            quote["quote_id"],
            idempotency_key=f"test:fulfillment:reserve:{self.suffix}",
        )
        order.reload()
        for payment in checkout_payment_rows(order):
            order.append("payments_plan", {**payment, "status": "Confirmed"})
        order.append("payments_plan", self._cash_payment("100"))
        order.save(ignore_permissions=True)

        routes = (
            FulfillmentRouteKey(
                provider_id="GSF",
                seller_company=self.company,
                provider_location=location,
                legal_entity_type="Company",
                legal_entity_name=self.company,
                fiscal_route="NON_FISCAL",
            ),
            FulfillmentRouteKey(
                provider_id="CC",
                seller_company=self.company,
                provider_location=f"{location}:certificate-route",
                legal_entity_type="Company",
                legal_entity_name=self.company,
                fiscal_route="NON_FISCAL",
            ),
        )
        order_row = order.items[0]
        refs = [
            ProviderAllocationRef(
                route=route,
                allocation_doctype="GSF Allocation" if route.provider_id == "GSF" else "CC Allocation",
                allocation_name=f"TEST-{route.provider_id}-{self.suffix}",
                item_code=order_row.item_code,
                qty=Decimal("1"),
                external_row_id=order_row.name,
                rate=Decimal("100"),
            )
            for route in routes
        ]
        with _service_write():
            checkout = frappe.get_doc(
                {
                    "doctype": "GSF Checkout",
                    "status": "ERP_SALE_SUBMITTED",
                    "company_group": company_group,
                    "physical_location": location,
                    "seller_company": self.company,
                    "customer": self.customer.name,
                    "idempotency_key": f"test:gift:fulfillment:{self.suffix}",
                    "sales_channel": "POS-UA",
                    "external_order_doctype": "POS Order",
                    "external_order_name": order.name,
                    "currency": "UAH",
                    "conversion_rate": 1,
                    "lines": [
                        {
                            "item_code": order_row.item_code,
                            "qty": 2,
                            "rate": 100,
                            "uom": order_row.uom,
                            "external_row_id": order_row.name,
                            "route_allocations": serialize_refs(refs),
                        }
                    ],
                }
            ).insert(ignore_permissions=True)

        payments = split_pos_payments(
            order,
            OrderedDict((route.stable_id, Decimal("100")) for route in routes),
            fixed_route_payments=sale_route_payment_components(order, checkout),
        )
        invoices = [
            self._submit_fulfillment_invoice(
                order,
                checkout,
                route,
                payments[route.stable_id],
            )
            for route in routes
        ]

        certificate.reload()
        reservation.reload()
        self.assertEqual(Decimal(str(certificate.current_balance)), Decimal("0.00"))
        self.assertEqual(reservation.status, "Consumed")
        allocations = frappe.get_all(
            "UA Gift Certificate Redemption Allocation",
            filters={"reservation": reservation.name},
            fields=["name", "sales_invoice", "qty", "certificate_amount"],
            order_by="creation",
        )
        self.assertEqual(len(allocations), 2)
        self.assertEqual(
            [(row.sales_invoice, Decimal(str(row.qty)), Decimal(str(row.certificate_amount))) for row in allocations],
            [(invoice.name, Decimal("1"), Decimal("50.00")) for invoice in invoices],
        )

        order.db_set(
            {
                "gsf_checkout": checkout.name,
                "sales_invoice": invoices[0].name,
                "sales_invoices_json": json.dumps([invoice.name for invoice in invoices]),
            },
            update_modified=False,
        )
        order.reload()
        return_order = self._return_order(order, 0)
        snapshot = json.loads(return_order.gift_certificate_snapshot_json)
        self.assertEqual(len(snapshot["components"]), 1)
        component = snapshot["components"][0]
        selected_invoice = next(
            invoice for invoice in invoices if invoice.name == component["sales_invoice"]
        )
        selected_route = next(
            route for route in routes if route.stable_id == selected_invoice.ua_fulfillment_route
        )
        return_payments = split_pos_payments(
            return_order,
            OrderedDict(((selected_route.stable_id, Decimal("100")),)),
            is_return=True,
            fixed_route_payments=return_route_payment_components(
                return_order,
                {selected_invoice.name: selected_route.stable_id},
            ),
        )
        return_invoice = self._submit_fulfillment_return_invoice(
            selected_invoice,
            return_order,
            checkout,
            selected_route,
            return_payments[selected_route.stable_id],
        )

        certificate.reload()
        self.assertEqual(Decimal(str(certificate.current_balance)), Decimal("50.00"))
        restored = frappe.get_all(
            "UA Gift Certificate Return Allocation",
            filters={"return_sales_invoice": return_invoice.name},
            fields=["original_redemption_allocation", "qty_returned", "certificate_amount_to_restore"],
        )
        self.assertEqual(len(restored), 1)
        original_allocation = next(
            row for row in allocations if row.sales_invoice == selected_invoice.name
        )
        self.assertEqual(restored[0].original_redemption_allocation, original_allocation.name)
        self.assertEqual(Decimal(str(restored[0].qty_returned)), Decimal("1"))
        self.assertEqual(
            Decimal(str(restored[0].certificate_amount_to_restore)),
            Decimal("50.00"),
        )

    def _base_context(self):
        desks = frappe.get_all("POS Cash Desk", fields=["name", "company", "warehouse", "default_customer"], limit=1)
        shifts = frappe.get_all("POS Operational Shift", pluck="name", limit=1)
        employees = frappe.get_all("Employee", pluck="name", limit=1)
        items = frappe.get_all(
            "Item",
            filters={"disabled": 0, "is_sales_item": 1},
            fields=["name", "stock_uom"],
            limit=1,
        )
        if not desks or not shifts or not employees or not items:
            self.skipTest("POS/Sales Invoice fixtures are unavailable")
        source = desks[0]
        self.company = source.company
        self.shift = shifts[0]
        self.employee = employees[0]
        self.item = items[0]
        self.customer = frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": f"Gift Certificate Test {self.suffix}",
                "customer_type": "Individual",
            }
        ).insert(ignore_permissions=True)
        self.desk = frappe.get_doc(
            {
                "doctype": "POS Cash Desk",
                "desk_name": f"Gift Certificate Test {self.suffix}",
                "status": "Active",
                "company": self.company,
                "warehouse": source.warehouse,
                "default_customer": source.default_customer or self.customer.name,
            }
        ).insert(ignore_permissions=True)
        self.fop_profile = None
        self.asset_account = self._account("Asset")
        self.liability_account = self._account("Liability")
        self.expense_account = self._account("Expense")
        self.cost_center = frappe.db.get_value(
            "Cost Center",
            {"company": self.company, "is_group": 0, "disabled": 0},
            "name",
        )
        if not self.cost_center:
            self.skipTest("Cost Center fixture is unavailable")

    def _account(self, root_type):
        currency = frappe.db.get_value("Company", self.company, "default_currency")
        account = frappe.db.get_value(
            "Account",
            {
                "company": self.company,
                "root_type": root_type,
                "is_group": 0,
                "disabled": 0,
                "account_currency": currency,
            },
            "name",
        )
        if not account:
            self.skipTest(f"{root_type} ledger account is unavailable")
        return account

    def _profiles(self):
        self.compliance = frappe.get_doc(
            {
                "doctype": "UA Gift Certificate Compliance Profile",
                "profile_name": f"GC Test Compliance {self.suffix}",
                "version": 1,
                "status": "Active",
                "company": self.company,
                "tax_regime_snapshot": "General system, non-VAT test fixture",
                "vat_status": "Non VAT",
                "valid_from": frappe.utils.today(),
                "allow_issue": 1,
                "allow_sale": 1,
                "allow_redemption_as_payment": 1,
                "allow_refund": 1,
                "prro_sale_mapping": '{"form":"OTHER"}',
                "prro_redemption_mapping": '{"code":100000}',
                "vat_mode": "Blocked",
                "legal_basis_file": "/files/gift-certificate-test-evidence.pdf",
                "legal_basis_reference": f"TEST-{self.suffix}",
                "approved_by": "Administrator",
                "approved_at": frappe.utils.now_datetime(),
            }
        ).insert(ignore_permissions=True)
        self.accounting = frappe.get_doc(
            {
                "doctype": "UA Gift Certificate Accounting Profile",
                "profile_name": f"GC Test Accounting {self.suffix}",
                "version": 1,
                "company": self.company,
                "valid_from": frappe.utils.today(),
                "status": "Active",
                "paid_liability_account": self.liability_account,
                "redemption_clearing_account": self.asset_account,
                "promotional_expense_account": self.expense_account,
                "settlement_receivable_account": self.asset_account,
                "settlement_payable_account": self.liability_account,
                "default_cost_center": self.cost_center,
                "certificate_sale_posting_mode": "Custom Sale Document + Journal Entry",
                "redemption_posting_mode": "Sales Invoice Payment Accounts",
                "expiry_posting_mode": "Keep Liability",
            }
        ).insert(ignore_permissions=True)
        self.network = frappe.get_doc(
            {
                "doctype": "UA Gift Certificate Network",
                "network_name": f"GC Test Network {self.suffix}",
                "status": "Active",
                "currency": "UAH",
                "valid_from": frappe.utils.today(),
                "locations": [
                    {
                        "location_type": "POS Cash Desk",
                        "location_name": self.desk.name,
                        "can_issue": 1,
                        "can_sell": 1,
                        "can_redeem": 1,
                        "can_return": 1,
                        "valid_from": frappe.utils.today(),
                    }
                ],
                "entities": [
                    {
                        "company": self.company,
                        "entity_role": "Both",
                        "accounting_profile": self.accounting.name,
                        "compliance_profile": self.compliance.name,
                        "valid_from": frappe.utils.today(),
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        self.program = frappe.get_doc(
            {
                "doctype": "UA Gift Certificate Program",
                "program_name": f"GC Test Program {self.suffix}",
                "network": self.network.name,
                "status": "Active",
                "version": 1,
                "currency": "UAH",
                "accounting_model": "Prepaid Payment",
                "usage_policy": "Multi Use Balance",
                "under_spend_policy": "Retain Balance",
                "denomination_mode": "Variable",
                "min_face_value": 1,
                "max_face_value": 10000,
                "denomination_step": 1,
                "sale_price_mode": "Manual",
                "allow_discounted_sale": 1,
                "activation_trigger": "On Full Payment",
                "validity_start_basis": "Activation Date",
                "validity_days": 365,
                "holder_mode": "Bearer",
                "max_redemption_percent_of_eligible_total": 100,
                "allow_multiple_certificates_per_order": 1,
                "max_certificates_per_order": 10,
                "restore_mode": "Same Certificate",
                "restored_validity_policy": "Preserve Original Expiry",
                "funding_consumption_policy": "Proportional",
                "loyalty_interaction_policy": "Certificate Sale Only",
                "expiry_accounting_policy": "Keep Liability",
                "accounting_profile": self.accounting.name,
                "compliance_profile": self.compliance.name,
                "policy_checksum": f"test-{self.suffix}",
            }
        ).insert(ignore_permissions=True)
        self.network.default_program = self.program.name
        self.network.save(ignore_permissions=True)
        self.paid_mode = self._mode("Paid Liability", self.liability_account)
        self.promotional_mode = self._mode("Promotional", self.expense_account)
        self.settlement_mode = self._mode("Settlement Receivable", self.asset_account)
        self.cash_mode = self._mode("None", self.asset_account)

    def _mode(self, component, account):
        mode = frappe.get_doc(
            {
                "doctype": "Mode of Payment",
                "mode_of_payment": f"GC {component} {self.suffix}",
                "type": "General",
                "enabled": 1,
                "ua_pos_kind": "Cash" if component == "None" else "Gift Certificate",
                "ua_gift_certificate_component": component,
                "ua_gift_certificate_network": self.network.name if component != "None" else None,
                "ua_gift_certificate_internal_only": 0 if component in {"None", "Paid Liability"} else 1,
                "ua_gift_certificate_accounting_profile": self.accounting.name if component != "None" else None,
                "accounts": [{"company": self.company, "default_account": account}],
            }
        ).insert(ignore_permissions=True)
        return mode

    def _enable_feature(self):
        settings = frappe.get_single("UA Gift Certificate Settings")
        settings.enabled = 0
        settings.pos_sale_enabled = 0
        settings.pos_redemption_enabled = 0
        settings.default_network = self.network.name
        settings.default_program = self.program.name
        settings.token_hmac_key_version = "v1"
        settings.save(ignore_permissions=True)
        frappe.db.set_single_value("UA Gift Certificate Settings", "stage0_status", "Passed")
        settings.reload()
        settings.enabled = 1
        settings.pos_sale_enabled = 1
        settings.pos_redemption_enabled = 1
        settings.save(ignore_permissions=True)
        frappe.clear_cache(doctype="UA Gift Certificate Settings")

    def _sale_order(self, *, qty, rate):
        return frappe.get_doc(
            {
                "doctype": "POS Order",
                "cash_desk": self.desk.name,
                "operational_shift": self.shift,
                "employee": self.employee,
                "customer": self.customer.name,
                "order_type": "Sale",
                "fiscal_mode": "Non Fiscal",
                "status": "Building",
                "items": [
                    {
                        "item_code": self.item.name,
                        "qty": qty,
                        "uom": self.item.stock_uom,
                        "rate": rate,
                        "warehouse": self.desk.warehouse,
                    }
                ],
            }
        ).insert(ignore_permissions=True)

    def _certificate_sale(self, certificate):
        return frappe.get_doc(
            {
                "doctype": "UA Gift Certificate Sale",
                "pos_order": self._sale_order(qty=1, rate=1).name,
                "cash_desk": self.desk.name,
                "operational_shift": self.shift,
                "employee": self.employee,
                "company": self.company,
                "posting_date": frappe.utils.today(),
                "posting_time": frappe.utils.nowtime(),
                "status": "Completed",
                "certificates": [
                    {
                        "certificate": certificate.name,
                        "program": certificate.program,
                        "holder_mode": certificate.holder_mode,
                        "face_value": certificate.face_value,
                        "sale_price": certificate.sale_price,
                        "paid_funding": certificate.initial_paid_funding,
                        "promotional_funding": certificate.initial_promotional_funding,
                    }
                ],
                "idempotency_key": f"test:sale:{certificate.name}",
            }
        ).insert(ignore_permissions=True)

    def _payment_row(self, mode, kind, amount):
        amount = Decimal(str(amount))
        return {
            "mode_of_payment": mode,
            "kind": kind,
            "prro_payment_form": "ГОТІВКА" if kind == "Cash" else "БЕЗГОТІВКОВА",
            "prro_payment_means": mode,
            "prro_payment_code": 0 if kind == "Cash" else 100000,
            "payment_context": "Звичайна оплата",
            "amount": amount,
            "tendered_amount": amount,
            "currency": "UAH",
            "status": "Confirmed",
        }

    def _cash_payment(self, amount):
        return self._payment_row(self.cash_mode.name, "Cash", amount)

    def _submit_sale_invoice(self, order):
        row = order.items[0]
        expected_payments = invoice_payments(order, is_return=False)
        self.assertTrue(expected_payments, "Gift Certificate checkout produced no Sales Invoice payments")
        invoice = frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "company": self.company,
                "customer": self.customer.name,
                "is_pos": 1,
                "update_stock": 0,
                "ua_pos_order": order.name,
                "ua_pos_desk": self.desk.name,
                "ua_pos_shift": self.shift,
                "items": [
                    {
                        "item_code": row.item_code,
                        "qty": row.qty,
                        "uom": row.uom,
                        "rate": row.rate,
                        "ua_pos_order_item": row.name,
                    }
                ],
            }
        )
        invoice.set_missing_values()
        invoice.set("payments", expected_payments)
        invoice.run_method("calculate_taxes_and_totals")
        prepare_invoice(invoice, order)
        self.assertTrue(invoice.ua_gift_certificate_context, "Gift Certificate invoice context was not prepared")
        self.assertTrue(invoice.payments, "Gift Certificate payments disappeared before insert")
        invoice.insert(ignore_permissions=True)
        self.assertTrue(invoice.payments, "Gift Certificate payments disappeared during insert")
        invoice.submit()
        return invoice

    def _submit_fulfillment_invoice(self, order, checkout, route, payments):
        row = order.items[0]
        invoice = frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "company": route.seller_company,
                "customer": self.customer.name,
                "is_pos": 1,
                "update_stock": 0,
                "ua_pos_order": order.name,
                "ua_pos_desk": self.desk.name,
                "ua_pos_shift": self.shift,
                "ua_sale_fulfillment": checkout.name,
                "ua_fulfillment_route": route.stable_id,
                "items": [
                    {
                        "item_code": row.item_code,
                        "qty": 1,
                        "uom": row.uom,
                        "rate": row.rate,
                        "ua_pos_order_item": row.name,
                    }
                ],
            }
        )
        invoice.set_missing_values()
        invoice.set("payments", payments)
        invoice.run_method("calculate_taxes_and_totals")
        prepare_invoice(invoice, order)
        invoice.insert(ignore_permissions=True)
        invoice.submit()
        allocation_field = "gsf_allocation" if route.provider_id == "GSF" else "cc_allocation"
        frappe.db.set_value(
            "Sales Invoice Item",
            invoice.items[0].name,
            allocation_field,
            f"TEST-{route.provider_id}-{self.suffix}",
            update_modified=False,
        )
        managed_field = "gsf_managed_sale" if route.provider_id == "GSF" else "cc_managed_sale"
        frappe.db.set_value(
            "Sales Invoice",
            invoice.name,
            managed_field,
            1,
            update_modified=False,
        )
        invoice.reload()
        return invoice

    def _submit_fulfillment_return_invoice(
        self,
        original_invoice,
        return_order,
        checkout,
        route,
        payments,
    ):
        original_row = original_invoice.items[0]
        pos_row = return_order.items[0]
        invoice = frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "company": route.seller_company,
                "customer": self.customer.name,
                "is_return": 1,
                "return_against": original_invoice.name,
                "is_pos": 1,
                "update_stock": 0,
                "ua_pos_order": return_order.name,
                "ua_pos_desk": self.desk.name,
                "ua_pos_shift": self.shift,
                "ua_sale_fulfillment": checkout.name,
                "ua_fulfillment_route": route.stable_id,
                "items": [
                    {
                        "item_code": original_row.item_code,
                        "qty": -1,
                        "uom": original_row.uom,
                        "rate": original_row.rate,
                        "sales_invoice_item": original_row.name,
                        "ua_pos_order_item": pos_row.name,
                    }
                ],
            }
        )
        invoice.set_missing_values()
        invoice.set("payments", payments)
        invoice.run_method("calculate_taxes_and_totals")
        prepare_invoice(invoice, return_order)
        invoice.insert(ignore_permissions=True)
        managed_field = "gsf_managed_sale" if route.provider_id == "GSF" else "cc_managed_sale"
        # The stock-managed return path is covered by the GSF/CC acceptance suites.
        # This fixture has route manifests but intentionally no synthetic stock layers.
        frappe.db.set_value(
            "Sales Invoice",
            original_invoice.name,
            managed_field,
            0,
            update_modified=False,
        )
        try:
            invoice.submit()
        finally:
            frappe.db.set_value(
                "Sales Invoice",
                original_invoice.name,
                managed_field,
                1,
                update_modified=False,
            )
        return invoice

    def _return_order(self, original_order, index):
        original_row = original_order.items[0]
        order = frappe.get_doc(
            {
                "doctype": "POS Order",
                "cash_desk": self.desk.name,
                "operational_shift": self.shift,
                "employee": self.employee,
                "customer": self.customer.name,
                "order_type": "Return",
                "return_against": original_order.name,
                "fiscal_mode": "Non Fiscal",
                "status": "Building",
                "idem_key": f"test:return:{self.suffix}:{index}",
                "items": [
                    {
                        "item_code": original_row.item_code,
                        "qty": 1,
                        "uom": original_row.uom,
                        "rate": original_row.rate,
                        "warehouse": original_row.warehouse,
                        "return_against_item": original_row.name,
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        prepare_return_order(order, original_order)
        for payment in return_payment_rows(order):
            order.append("payments_plan", {**payment, "status": "Confirmed"})
        payout = Decimal(str(order.grand_total)) - Decimal(str(order.gift_certificate_redeemed_total))
        if payout:
            order.append("payments_plan", self._cash_payment(payout))
        order.save(ignore_permissions=True)
        return order

    def _submit_return_invoice(self, original_invoice, return_order):
        original_row = original_invoice.items[0]
        pos_row = return_order.items[0]
        invoice = frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "company": self.company,
                "customer": self.customer.name,
                "is_return": 1,
                "return_against": original_invoice.name,
                "is_pos": 1,
                "update_stock": 0,
                "ua_pos_order": return_order.name,
                "ua_pos_desk": self.desk.name,
                "ua_pos_shift": self.shift,
                "items": [
                    {
                        "item_code": original_row.item_code,
                        "qty": -1,
                        "uom": original_row.uom,
                        "rate": original_row.rate,
                        "sales_invoice_item": original_row.name,
                        "ua_pos_order_item": pos_row.name,
                    }
                ],
            }
        )
        invoice.set_missing_values()
        invoice.set("payments", invoice_payments(return_order, is_return=True))
        invoice.run_method("calculate_taxes_and_totals")
        prepare_invoice(invoice, return_order)
        invoice.insert(ignore_permissions=True)
        invoice.submit()
        return invoice

    def _active_full_value_certificate(self):
        return self._active_certificate("100", "full")

    def _active_certificate(self, amount, label):
        certificate, _token = issue_certificate(
            program_name=self.program.name,
            face_value=amount,
            sale_price=amount,
            issuer_company=self.company,
            idempotency_key=f"test:{label}:issue:{self.suffix}",
        )
        sale = self._certificate_sale(certificate)
        activate_certificate(
            certificate.name,
            sale_reference=sale.name,
            payment_evidence=f"test:{label}:payment:{self.suffix}",
            idempotency_key=f"test:{label}:activate:{self.suffix}",
        )
        certificate.reload()
        return certificate

    def _gl_total(self, voucher, account, field):
        value = frappe.db.sql(
            f"""select coalesce(sum({field}), 0)
                from `tabGL Entry`
                where voucher_type='Sales Invoice' and voucher_no=%s and account=%s""",
            (voucher, account),
        )[0][0]
        return Decimal(str(value)).quantize(Decimal("0.01"))
