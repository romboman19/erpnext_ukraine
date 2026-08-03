from decimal import Decimal

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_ua.ua_loyalty.adapters.sales_invoice import on_submit as loyalty_on_submit
from erpnext_ua.ua_loyalty.adapters.sales_invoice import prepare_invoice, validate_before_submit
from erpnext_ua.ua_loyalty.api import approve_adjustment, card_action, request_adjustment
from erpnext_ua.ua_loyalty.exceptions import LoyaltyConflict
from erpnext_ua.ua_loyalty.scheduler import _activate_lot, _expire
from erpnext_ua.ua_loyalty.services.account_service import account_for
from erpnext_ua.ua_loyalty.services.import_service import run_import
from erpnext_ua.ua_loyalty.services.ledger_service import append_ledger
from erpnext_ua.ua_loyalty.services.quote_service import quote_order, resolve_location
from erpnext_ua.ua_loyalty.services.reconciliation_service import reconcile_account
from erpnext_ua.ua_loyalty.services.reservation_service import mark_payment_in_progress, release, reserve


class TestLoyaltyServices(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        suffix = frappe.generate_hash(length=8)
        self.customer = frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": f"Loyalty Test {suffix}",
                "customer_type": "Individual",
            }
        ).insert(ignore_permissions=True)
        self.scope = frappe.get_doc(
            {
                "doctype": "UA Loyalty Scope",
                "scope_code": f"TEST-{suffix}",
                "scope_name": f"Test Scope {suffix}",
                "currency": "UAH",
                "active": 1,
            }
        ).insert(ignore_permissions=True)
        self.program = frappe.get_doc(
            {
                "doctype": "UA Loyalty Program",
                "program_code": f"PROGRAM-{suffix}",
                "program_name": f"Test Program {suffix}",
                "scope": self.scope.name,
                "active": 1,
                "tiers": [
                    {"tier_code": "BASE", "threshold_amount": 0, "earn_percent": 1},
                    {"tier_code": "PLUS", "threshold_amount": 1000, "earn_percent": 3},
                ],
            }
        ).insert(ignore_permissions=True)
        self.scope.default_program = self.program.name
        self.scope.save(ignore_permissions=True)
        self.account = account_for(self.customer.name, self.scope.name, create=True)

    def _entry(self, amount, key):
        return append_ledger(
            self.account,
            entry_type="MANUAL_CREDIT" if amount >= 0 else "MANUAL_DEBIT",
            active_delta=Decimal(str(amount)),
            idempotency_key=key,
            source_doctype="Customer",
            source_name=self.customer.name,
        )

    def test_signed_balance_debt_offset_and_reconciliation(self):
        self._entry(100, "test:credit:100")
        self._entry(-80, "test:debit:80")
        self._entry(-60, "test:debit:60")
        self.account.reload()
        self.assertEqual(Decimal(str(self.account.marketing_balance)), Decimal("-40.00"))
        self.assertEqual(Decimal(str(self.account.debt_balance)), Decimal("40.00"))
        self.assertEqual(Decimal(str(self.account.redeemable_balance)), Decimal("0.00"))

        credit = self._entry(55, "test:credit:55")
        self.account.reload()
        self.assertEqual(Decimal(str(self.account.marketing_balance)), Decimal("15.00"))
        self.assertEqual(Decimal(str(self.account.debt_balance)), Decimal("0.00"))
        lot = frappe.get_doc("UA Loyalty Bonus Lot", {"source_ledger_entry": credit.name})
        self.assertEqual(Decimal(str(lot.debt_offset_amount)), Decimal("40.00"))
        self.assertEqual(Decimal(str(lot.available_amount)), Decimal("15.00"))
        self.assertEqual(reconcile_account(self.account.name)["status"], "OK")

    def test_ledger_idempotency_and_payload_conflict(self):
        first = self._entry(10, "test:idempotency")
        second = self._entry(10, "test:idempotency")
        self.assertEqual(first.name, second.name)
        self.assertRaises(LoyaltyConflict, self._entry, 11, "test:idempotency")

    def test_reconciliation_detects_mismatch_without_silent_repair(self):
        self._entry(10, "test:reconciliation-source")
        frappe.db.set_value("UA Loyalty Account", self.account.name, "marketing_balance", 999, update_modified=False)
        result = reconcile_account(self.account.name)
        self.account.reload()
        self.assertEqual(result["status"], "MISMATCH")
        self.assertEqual(Decimal(str(self.account.marketing_balance)), Decimal("999.00"))
        repaired = reconcile_account(self.account.name, repair=True)
        self.account.reload()
        self.assertTrue(repaired["repaired"])
        self.assertEqual(Decimal(str(self.account.marketing_balance)), Decimal("10.00"))

    def test_account_is_unique_per_customer_and_scope(self):
        duplicate = frappe.get_doc(
            {
                "doctype": "UA Loyalty Account",
                "customer": self.customer.name,
                "scope": self.scope.name,
                "program": self.program.name,
                "status": "ACTIVE",
            }
        )
        self.assertRaises(frappe.ValidationError, duplicate.insert, ignore_permissions=True)

    def test_same_customer_has_independent_balances_in_separate_scopes(self):
        suffix = frappe.generate_hash(length=8)
        second_scope = frappe.get_doc(
            {
                "doctype": "UA Loyalty Scope",
                "scope_code": f"SECOND-{suffix}",
                "scope_name": f"Second Scope {suffix}",
                "currency": "UAH",
                "active": 1,
            }
        ).insert(ignore_permissions=True)
        second_program = frappe.copy_doc(self.program)
        second_program.program_code = f"SECOND-PROGRAM-{suffix}"
        second_program.program_name = f"Second Program {suffix}"
        second_program.scope = second_scope.name
        second_program.published_snapshot_hash = None
        second_program.insert(ignore_permissions=True)
        second_scope.default_program = second_program.name
        second_scope.save(ignore_permissions=True)
        second_account = account_for(self.customer.name, second_scope.name, create=True)
        self._entry(25, "test:first-scope-only")
        second_account.reload()
        self.assertEqual(Decimal(str(second_account.marketing_balance)), Decimal("0.00"))

    def test_location_mapping_is_temporal(self):
        desk, _shift, _employee, _item = self._isolated_pos_context()
        old_scope = self.scope.name
        suffix = frappe.generate_hash(length=8)
        current_scope = frappe.get_doc(
            {
                "doctype": "UA Loyalty Scope",
                "scope_code": f"CURRENT-{suffix}",
                "scope_name": f"Current Scope {suffix}",
                "currency": "UAH",
                "active": 1,
            }
        ).insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "UA Loyalty Location",
                "scope": old_scope,
                "pos_cash_desk": desk.name,
                "valid_from": frappe.utils.add_days(frappe.utils.now_datetime(), -30),
                "valid_to": frappe.utils.add_days(frappe.utils.now_datetime(), -1),
                "active": 1,
            }
        ).insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "UA Loyalty Location",
                "scope": current_scope.name,
                "pos_cash_desk": desk.name,
                "valid_from": frappe.utils.now_datetime(),
                "active": 1,
            }
        ).insert(ignore_permissions=True)
        self.assertEqual(resolve_location(cash_desk=desk.name).scope, current_scope.name)

    def test_expiry_never_creates_debt(self):
        entry = append_ledger(
            self.account,
            entry_type="MANUAL_CREDIT",
            active_delta=Decimal("10"),
            idempotency_key="test:expiring-credit",
            source_doctype="Customer",
            source_name=self.customer.name,
            values={
                "expires_at": frappe.utils.add_days(frappe.utils.now_datetime(), -1),
                "expiry_writeoff_mode": "AGGREGATE_NOMINAL_CAP",
            },
        )
        self._entry(-7, "test:spent-before-expiry")
        obligation = frappe.get_doc("UA Loyalty Expiry Obligation", {"source_ledger_entry": entry.name})
        _expire(obligation.name)
        self.account.reload()
        obligation.reload()
        self.assertEqual(Decimal(str(self.account.marketing_balance)), Decimal("0.00"))
        self.assertEqual(Decimal(str(self.account.debt_balance)), Decimal("0.00"))
        self.assertEqual(Decimal(str(obligation.processed_amount)), Decimal("3.00"))

    def test_pending_credit_creates_expiry_only_after_activation(self):
        entry = append_ledger(
            self.account,
            entry_type="EARN_PENDING",
            pending_delta=Decimal("12"),
            idempotency_key="test:pending-credit",
            source_doctype="Customer",
            source_name=self.customer.name,
            values={
                "effective_datetime": frappe.utils.add_days(frappe.utils.now_datetime(), -1),
                "expires_at": frappe.utils.add_days(frappe.utils.now_datetime(), 30),
                "expiry_writeoff_mode": "AGGREGATE_NOMINAL_CAP",
            },
        )
        self.assertFalse(frappe.db.exists("UA Loyalty Expiry Obligation", {"source_ledger_entry": entry.name}))
        lot = frappe.get_doc("UA Loyalty Bonus Lot", {"source_ledger_entry": entry.name})
        _activate_lot(lot.name)
        activation = frappe.get_doc(
            "UA Loyalty Ledger Entry", {"original_entry": entry.name, "entry_type": "EARN_ACTIVATE"}
        )
        self.assertTrue(frappe.db.exists("UA Loyalty Expiry Obligation", {"source_ledger_entry": activation.name}))

    def test_two_pos_quotes_cannot_reserve_the_same_balance(self):
        desk, shift, employee, item = self._isolated_pos_context()
        self._enable_pos_loyalty(desk.name)
        self._entry(100, "test:pos-opening-credit")

        first = self._pos_order(desk, shift, employee, item)
        second = self._pos_order(desk, shift, employee, item)
        first_quote = quote_order(first, Decimal("80"))
        second_quote = quote_order(second, Decimal("80"))
        reservation = reserve(first, quote_hash=first_quote["quote_hash"], idempotency_key="test:reserve:first")
        self.assertEqual(Decimal(str(reservation.reserved_amount)), Decimal("80.00"))
        with self.assertRaisesRegex(Exception, "Баланс змінився після quote"):
            reserve(second, quote_hash=second_quote["quote_hash"], idempotency_key="test:reserve:second")
        mark_payment_in_progress(first, reservation)
        with self.assertRaisesRegex(Exception, "Стан зовнішньої оплати ще не визначено"):
            release(reservation.name)
        released = release(
            reservation.name,
            allow_payment_in_progress=True,
            reason_code="PAYMENT_FAILED",
            idempotency_key="test:release:first",
        )
        self.assertEqual(released.status, "RELEASED")
        self.assertEqual(released.release_reason_code, "PAYMENT_FAILED")
        self.assertEqual(released.release_idempotency_key, "test:release:first")
        self.assertEqual(
            release(
                reservation.name,
                reason_code="PAYMENT_FAILED",
                idempotency_key="test:release:first",
            ).name,
            reservation.name,
        )
        with self.assertRaises(LoyaltyConflict):
            release(
                reservation.name,
                reason_code="CASHIER_CANCELLED",
                idempotency_key="test:release:first",
            )

    def _pos_order(self, desk, shift, employee, item, *, qty=1, rate=100):
        return frappe.get_doc(
            {
                "doctype": "POS Order",
                "cash_desk": desk.name,
                "operational_shift": shift,
                "employee": employee,
                "customer": self.customer.name,
                "order_type": "Sale",
                "fiscal_mode": "Non Fiscal",
                "status": "Building",
                "items": [
                    {
                        "item_code": item,
                        "qty": qty,
                        "uom": frappe.db.get_value("Item", item, "stock_uom"),
                        "rate": rate,
                        "warehouse": desk.warehouse,
                    }
                ],
            }
        ).insert(ignore_permissions=True)

    def test_import_preserves_negative_opening_balance(self):
        batch = frappe.get_doc(
            {
                "doctype": "UA Loyalty Import Batch",
                "source_label": "negative balance acceptance",
                "dry_run": 0,
                "source_data": frappe.as_json(
                    [
                        {
                            "customer": self.customer.name,
                            "scope": self.scope.name,
                            "program": self.program.name,
                            "marketing_balance": "-25.50",
                            "metric_balance": "120.00",
                        }
                    ]
                ),
            }
        ).insert(ignore_permissions=True)
        result = run_import(batch.name)
        self.account.reload()
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(Decimal(str(self.account.marketing_balance)), Decimal("-25.50"))
        self.assertEqual(Decimal(str(self.account.debt_balance)), Decimal("25.50"))
        self.assertEqual(Decimal(str(self.account.metric_balance)), Decimal("120.00"))

    def test_card_action_is_audited_and_idempotent(self):
        card = frappe.get_doc(
            {
                "doctype": "UA Loyalty Card",
                "account": self.account.name,
                "barcode": f"CARD-{frappe.generate_hash(length=10)}",
                "status": "ACTIVE",
                "is_primary": 1,
            }
        ).insert(ignore_permissions=True)
        first = card_action(card.name, "BLOCK", "fraud review", idempotency_key="test:card:block")
        second = card_action(card.name, "BLOCK", "fraud review", idempotency_key="test:card:block")
        card.reload()
        self.assertEqual(card.status, "BLOCKED")
        self.assertFalse(first["existing"])
        self.assertTrue(second["existing"])
        self.assertEqual(first["change_log"], second["change_log"])

    def test_large_manual_adjustment_requires_another_approver(self):
        config = frappe.get_single("UA Loyalty Settings")
        config.dual_control_threshold = 100
        config.save(ignore_permissions=True)
        frappe.clear_cache(doctype="UA Loyalty Settings")
        adjustment = frappe.get_doc(
            {
                "doctype": "UA Loyalty Adjustment",
                "account": self.account.name,
                "operation": "CREDIT",
                "amount": 150,
                "reason_code": "CUSTOMER_CARE",
                "comment": "Acceptance test",
            }
        ).insert(ignore_permissions=True)
        request_adjustment(adjustment.name)
        self.assertRaises(frappe.ValidationError, approve_adjustment, adjustment.name)

        approver = self._manager_user()
        original_user = frappe.session.user
        try:
            frappe.set_user(approver.name)
            result = approve_adjustment(adjustment.name)
        finally:
            frappe.set_user(original_user)
        self.account.reload()
        self.assertEqual(result.status, "POSTED")
        self.assertEqual(Decimal(str(self.account.marketing_balance)), Decimal("150.00"))

    def test_standard_and_ua_loyalty_cannot_post_together(self):
        document = frappe._dict(
            ua_pos_order=None,
            ua_loyalty_account=self.account.name,
            ua_loyalty_snapshot_hash="snapshot",
            ua_loyalty_snapshot_json="{}",
            redeem_loyalty_points=1,
            loyalty_points=1,
            loyalty_amount=1,
            loyalty_program="STANDARD",
        )
        with self.assertRaisesRegex(frappe.ValidationError, "Стандартну та UA Loyalty"):
            validate_before_submit(document)

    def _manager_user(self):
        email = f"loyalty-manager-{frappe.generate_hash(length=8)}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Loyalty",
                "last_name": "Manager",
                "enabled": 1,
                "send_welcome_email": 0,
            }
        ).insert(ignore_permissions=True)
        user.add_roles("UA Loyalty Manager")
        return user

    def test_sale_two_partial_returns_restore_exact_original_allocations(self):
        desk, shift, employee, item = self._isolated_pos_context()
        self._enable_pos_loyalty(desk.name)
        self._entry(100, "test:return-opening-credit")
        order = self._pos_order(desk, shift, employee, item)
        order.items[0].qty = 2
        order.save(ignore_permissions=True)
        quoted = quote_order(order, Decimal("80"))
        reserve(order, quote_hash=quoted["quote_hash"], idempotency_key="test:return-reservation")
        order.reload()

        sale = self._sales_invoice(order, desk)
        self.account.reload()
        self.assertEqual(Decimal(str(self.account.marketing_balance)), Decimal("21.20"))
        loyalty_on_submit(frappe.get_doc("Sales Invoice", sale.name))
        self.account.reload()
        self.assertEqual(Decimal(str(self.account.marketing_balance)), Decimal("21.20"))

        first_return = self._return_invoice(order, sale, desk, shift, employee, "first")
        self.account.reload()
        self.assertEqual(Decimal(str(self.account.marketing_balance)), Decimal("60.60"))
        second_return = self._return_invoice(order, sale, desk, shift, employee, "second")
        self.account.reload()
        self.assertEqual(Decimal(str(self.account.marketing_balance)), Decimal("100.00"))

        restores = frappe.get_all(
            "UA Loyalty Ledger Entry",
            filters={
                "source_name": ("in", [first_return.name, second_return.name]),
                "entry_type": "REDEEM_RETURN_RESTORE",
            },
            fields=["active_delta"],
        )
        reversals = frappe.get_all(
            "UA Loyalty Ledger Entry",
            filters={
                "source_name": ("in", [first_return.name, second_return.name]),
                "entry_type": "EARN_REVERSE_RETURN",
            },
            fields=["active_delta"],
        )
        self.assertEqual(sum((Decimal(str(row.active_delta)) for row in restores), Decimal("0")), Decimal("80.00"))
        self.assertEqual(sum((Decimal(str(row.active_delta)) for row in reversals), Decimal("0")), Decimal("-1.20"))

    def test_return_of_already_spent_earn_creates_debt(self):
        desk, shift, employee, item = self._isolated_pos_context()
        self._enable_pos_loyalty(desk.name)

        earning_order = self._pos_order(desk, shift, employee, item, rate=1000)
        quote_order(earning_order, Decimal("0"))
        earning_order.reload()
        earning_sale = self._sales_invoice(earning_order, desk)
        self.account.reload()
        self.assertEqual(Decimal(str(self.account.marketing_balance)), Decimal("10.00"))

        spending_order = self._pos_order(desk, shift, employee, item, rate=10)
        spending_quote = quote_order(spending_order, Decimal("10"))
        reserve(
            spending_order,
            quote_hash=spending_quote["quote_hash"],
            idempotency_key="test:spend-earned-reservation",
        )
        spending_order.reload()
        self._sales_invoice(spending_order, desk)
        self.account.reload()
        self.assertEqual(Decimal(str(self.account.marketing_balance)), Decimal("0.00"))

        self._return_invoice(earning_order, earning_sale, desk, shift, employee, "earned-spent")
        self.account.reload()
        self.assertEqual(Decimal(str(self.account.marketing_balance)), Decimal("-10.00"))
        self.assertEqual(Decimal(str(self.account.debt_balance)), Decimal("10.00"))
        self.assertEqual(Decimal(str(self.account.redeemable_balance)), Decimal("0.00"))

    def _isolated_pos_context(self):
        source = frappe.get_all("POS Cash Desk", fields=["company", "warehouse", "default_customer"], limit=1)
        shift = frappe.get_all("POS Operational Shift", pluck="name", limit=1)
        employee = frappe.get_all("Employee", pluck="name", limit=1)
        item = frappe.get_all("Item", filters={"disabled": 0, "is_sales_item": 1}, pluck="name", limit=1)
        if not source or not shift or not employee or not item:
            self.skipTest("POS/Sales Invoice fixtures are unavailable")
        desk = frappe.get_doc(
            {
                "doctype": "POS Cash Desk",
                "desk_name": f"Loyalty Test {frappe.generate_hash(length=8)}",
                "status": "Active",
                "company": source[0].company,
                "warehouse": source[0].warehouse,
                "default_customer": source[0].default_customer or self.customer.name,
            }
        ).insert(ignore_permissions=True)
        return desk, shift[0], employee[0], item[0]

    def _enable_pos_loyalty(self, desk):
        config = frappe.get_single("UA Loyalty Settings")
        config.enabled = 1
        config.execution_mode = "POS_ONLY"
        config.save(ignore_permissions=True)
        frappe.clear_cache(doctype="UA Loyalty Settings")
        frappe.get_doc(
            {
                "doctype": "UA Loyalty Location",
                "scope": self.scope.name,
                "pos_cash_desk": desk,
                "priority": 1,
                "active": 1,
            }
        ).insert(ignore_permissions=True)

    def _sales_invoice(self, order, desk):
        row = order.items[0]
        gross = Decimal(str(row.qty)) * Decimal(str(row.rate))
        discount_percentage = Decimal(str(row.discount_amount or 0)) * Decimal("100") / gross if gross else Decimal("0")
        invoice = frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "company": desk.company,
                "customer": order.customer,
                "is_pos": 0,
                "update_stock": 0,
                "ua_pos_order": order.name,
                "ua_pos_desk": desk.name,
                "ua_pos_shift": order.operational_shift,
                "items": [
                    {
                        "item_code": row.item_code,
                        "qty": row.qty,
                        "uom": row.uom,
                        "rate": row.rate,
                        "price_list_rate": row.rate,
                        "discount_percentage": discount_percentage,
                        "ua_pos_order_item": row.name,
                    }
                ],
            }
        )
        invoice.set_missing_values()
        prepare_invoice(invoice, order)
        invoice.insert(ignore_permissions=True)
        invoice.submit()
        return invoice

    def _return_invoice(self, original_order, sale, desk, shift, employee, suffix):
        original_row = original_order.items[0]
        original_qty = Decimal(str(original_row.qty))
        loyalty_share = Decimal(str(original_row.loyalty_redeemed_amount or 0)) / original_qty
        other_share = Decimal(str(original_row.non_loyalty_discount_amount or 0)) / original_qty
        gross = original_qty * Decimal(str(original_row.rate))
        discount_percentage = (
            Decimal(str(original_row.discount_amount or 0)) * Decimal("100") / gross if gross else Decimal("0")
        )
        return_order = frappe.get_doc(
            {
                "doctype": "POS Order",
                "cash_desk": desk.name,
                "operational_shift": shift,
                "employee": employee,
                "customer": original_order.customer,
                "order_type": "Return",
                "return_against": original_order.name,
                "fiscal_mode": "Non Fiscal",
                "status": "Building",
                "idem_key": f"test:return:{suffix}",
                "items": [
                    {
                        "item_code": original_row.item_code,
                        "qty": 1,
                        "uom": original_row.uom,
                        "rate": original_row.rate,
                        "warehouse": original_row.warehouse,
                        "non_loyalty_discount_amount": other_share,
                        "loyalty_redeemed_amount": loyalty_share,
                        "return_against_item": original_row.name,
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        sale.reload()
        return_invoice = frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "company": desk.company,
                "customer": original_order.customer,
                "is_pos": 0,
                "is_return": 1,
                "return_against": sale.name,
                "update_stock": 0,
                "ua_pos_order": return_order.name,
                "ua_pos_desk": desk.name,
                "ua_pos_shift": shift,
                "items": [
                    {
                        "item_code": original_row.item_code,
                        "qty": -1,
                        "uom": original_row.uom,
                        "rate": original_row.rate,
                        "price_list_rate": original_row.rate,
                        "discount_percentage": discount_percentage,
                        "sales_invoice_item": sale.items[0].name,
                        "ua_pos_order_item": return_order.items[0].name,
                    }
                ],
            }
        )
        return_invoice.set_missing_values()
        prepare_invoice(return_invoice, return_order)
        return_invoice.insert(ignore_permissions=True)
        return_invoice.submit()
        return return_invoice
