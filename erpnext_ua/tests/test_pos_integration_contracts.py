from __future__ import annotations

import json
import unittest
from pathlib import Path

import erpnext_ua.hooks as hooks


APP = Path(__file__).resolve().parents[1]


class TestPOSIntegrationContracts(unittest.TestCase):
    def test_upgrade_creates_module_before_pos_page(self):
        install_source = (APP / "install.py").read_text(encoding="utf-8")
        self.assertIn("def ensure_app_modules():", install_source)
        self.assertIn('"module_name": module_name', install_source)
        self.assertIn('"app_name": "erpnext_ua"', install_source)
        self.assertIn("def ensure_pos_workspace():", install_source)
        self.assertIn('"workspace_sidebar", "ua_pos_workspace.json"', install_source)
        self.assertIn('"desktop_icon", "ua_pos_workspace.json"', install_source)
        self.assertIn("import_file_by_path(path, force=True)", install_source)

        modules_hook = "erpnext_ua.install.ensure_app_modules"
        workspace_hook = "erpnext_ua.install.ensure_pos_workspace"
        page_hook = "erpnext_ua.install.ensure_pos_page"
        self.assertLess(hooks.before_migrate.index(modules_hook), hooks.before_migrate.index(workspace_hook))
        self.assertLess(hooks.after_install.index(modules_hook), hooks.after_install.index(page_hook))
        self.assertLess(hooks.after_install.index(workspace_hook), hooks.after_install.index(page_hook))
        self.assertLess(hooks.after_migrate.index(modules_hook), hooks.after_migrate.index(page_hook))
        self.assertLess(hooks.after_migrate.index(workspace_hook), hooks.after_migrate.index(page_hook))

    def test_pos_uses_policy_aware_identification_endpoint(self):
        source = (APP / "ua_pos" / "page" / "ua_pos" / "ua_pos.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('identificationApi("begin_pos"', source)
        self.assertIn("config.pos_channel", source)
        self.assertIn("config.allow_pos_channel_selection", source)
        self.assertNotIn('identificationApi("begin",', source)

    def test_employee_login_uses_plain_auto_generated_ean13(self):
        install = (APP / "install.py").read_text(encoding="utf-8")
        api = (APP / "ua_pos" / "api.py").read_text(encoding="utf-8")
        barcode = (APP / "ua_pos" / "employee_barcode.py").read_text(encoding="utf-8")

        self.assertIn('"fieldname": "ua_pos_barcode"', install)
        self.assertIn('"label": "Штрихкод касира (EAN-13)"', install)
        self.assertIn('"read_only": 1', install)
        self.assertIn("backfill_employee_barcodes()", install)
        self.assertIn('"ua_pos_barcode": barcode', api)
        self.assertNotIn('"ua_pos_barcode_hash": digest(barcode)', api)
        self.assertIn('EAN13_PREFIX = "9910"', barcode)
        self.assertIn('NAMING_SERIES = f"{EAN13_PREFIX}.{SEQUENCE_DIGITS *', barcode)
        self.assertEqual(
            hooks.doc_events["Employee"]["before_validate"],
            "erpnext_ua.ua_pos.employee_barcode.assign_employee_barcode",
        )

    def test_pos_login_has_no_test_cashier_and_uses_a_dark_heading(self):
        source = (APP / "ua_pos" / "page" / "ua_pos" / "ua_pos.js").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("POS-TEST-CASHIER", source)
        self.assertNotIn("Тестовий касир", source)
        self.assertIn(".ua-pos-login-card h1{font-size:27px;margin:32px 0 6px;color:var(--ink)}", source)

    def test_pos_workspace_is_visible_and_opens_the_cashier_page(self):
        workspace = json.loads(
            (
                APP
                / "ua_pos"
                / "workspace"
                / "ua_pos_workspace"
                / "ua_pos_workspace.json"
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(workspace["public"])
        self.assertFalse(workspace["is_hidden"])
        self.assertEqual(workspace["name"], "UA POS Workspace")
        self.assertTrue(
            any(
                link.get("link_type") == "Page"
                and link.get("link_to") == "ua-pos"
                for link in workspace["links"]
            )
        )

        icon = json.loads(
            (APP / "desktop_icon" / "ua_pos_workspace.json").read_text(encoding="utf-8")
        )
        self.assertEqual(icon["parent_icon"], "ERPNext Ukraine")
        self.assertEqual(icon["link_to"], workspace["name"])
        self.assertFalse(icon["hidden"])

        sidebar = json.loads(
            (APP / "workspace_sidebar" / "ua_pos_workspace.json").read_text(encoding="utf-8")
        )
        self.assertEqual(sidebar["name"], workspace["name"])

    def test_denomination_recount_widget_is_shared_everywhere_it_appears(self):
        source = (APP / "ua_pos" / "page" / "ua_pos" / "ua_pos.js").read_text(
            encoding="utf-8"
        )

        # Shift open/close and cash operations (incassation/expense) must render the
        # bill-count table through the same helpers, not through separate copies.
        self.assertEqual(source.count("function denominationTableHtml("), 1)
        self.assertEqual(source.count("function bindDenominationTable("), 1)
        self.assertEqual(source.count("function readDenominationRows("), 1)

        denomination_dialog = source[source.index("function denominationDialog(") :]
        denomination_dialog = denomination_dialog[: denomination_dialog.index("function openShift(")]
        self.assertIn("denominationTableHtml()", denomination_dialog)
        self.assertIn("bindDenominationTable(dialog.$wrapper)", denomination_dialog)
        self.assertIn("readDenominationRows(dialog.$wrapper)", denomination_dialog)

        cash_operation_dialog = source[source.index("function cashOperationDialog(") :]
        self.assertIn("denominationTableHtml()", cash_operation_dialog)
        self.assertIn("bindDenominationTable(dialog.$wrapper", cash_operation_dialog)
        self.assertIn("readDenominationRows(dialog.$wrapper)", cash_operation_dialog)
        self.assertIn('DENOMINATION_RECOUNT_MOVEMENT_TYPES.has(movementType)', cash_operation_dialog)

        self.assertIn(
            'const DENOMINATION_RECOUNT_MOVEMENT_TYPES = new Set(["Incassation Out", "Expense"]);',
            source,
        )

    def test_cash_operation_api_accepts_and_validates_denomination_counts(self):
        source = (APP / "ua_pos" / "api.py").read_text(encoding="utf-8")

        self.assertIn(
            'DENOMINATION_CONTEXT_BY_MOVEMENT_TYPE = {\n\t"Expense": "Expense",\n\t"Incassation Out": "Incassation",\n}',
            source,
        )
        cash_operation = source[source.index("def cash_operation(") :]
        cash_operation = cash_operation[: cash_operation.index("\n@frappe.whitelist(", 1)]
        self.assertIn("denominations=None", cash_operation)
        self.assertIn("rows = parse_rows(denominations) if denominations else []", cash_operation)
        self.assertIn("if rows and movement_type not in DENOMINATION_CONTEXT_BY_MOVEMENT_TYPE:", cash_operation)
        self.assertIn("if rows and abs(_count_total(rows) - amount) > 0.01:", cash_operation)
        self.assertIn('"denomination_counts": [', cash_operation)

    def test_completed_checkouts_reset_the_cart_through_a_single_shared_flow(self):
        source = (APP / "ua_pos" / "page" / "ua_pos" / "ua_pos.js").read_text(
            encoding="utf-8"
        )

        # A finished chek (cash/cashless payment, fully certificate-funded payment,
        # mixed payment, a return payout, or a resolved "Payment Unknown") must always
        # clear the cart through finishOrderFlow, not a one-off renderOrder() call.
        self.assertEqual(source.count("async function finishOrderFlow("), 1)
        self.assertIn(
            'const FINAL_ORDER_STATUSES = new Set(["Completed", "Completed Print Error"]);',
            source,
        )
        self.assertEqual(source.count("await finishOrderFlow("), 5)
        self.assertNotIn("renderOrder(completed); dialog.hide();", source)

        # "Друк чека" must still reach the chek that was just paid even after the cart
        # has already moved on to a fresh one.
        self.assertIn("function printableOrderName()", source)
        self.assertIn("state.lastCompletedOrder", source)

    def test_receipt_printer_defaults_to_a_cyrillic_code_page_confirmed_for_xprinter(self):
        doctype = json.loads(
            (APP / "ua_pos" / "doctype" / "pos_printer" / "pos_printer.json").read_text(
                encoding="utf-8"
            )
        )
        encoding_field = next(f for f in doctype["fields"] if f["fieldname"] == "encoding")
        code_page_field = next(f for f in doctype["fields"] if f["fieldname"] == "code_page")
        self.assertEqual(encoding_field["default"], "cp866")
        self.assertEqual(code_page_field["default"], "17")

        print_service = (APP / "ua_pos" / "print_service.py").read_text(encoding="utf-8")
        self.assertIn('encoding: str = "cp866", code_page: int = 17', print_service)

    def test_shift_report_lets_a_cashier_expand_a_sale_and_reprint_it(self):
        source = (APP / "ua_pos" / "page" / "ua_pos" / "ua_pos.js").read_text(
            encoding="utf-8"
        )

        report_section = source[source.index("function orderRowsHtml(") :]
        report_section = report_section[: report_section.index("function bindOrderRowsHandlers(")]
        self.assertIn("js-order-row", report_section)
        self.assertIn("js-order-items", report_section)
        self.assertIn("js-print-order", report_section)
        self.assertIn("order.customer", report_section)
        self.assertIn("order.fiscal_mode", report_section)

        bind_handlers = source[source.index("function bindOrderRowsHandlers(") :]
        bind_handlers = bind_handlers[: bind_handlers.index("\n  }\n") + len("\n  }\n")]
        self.assertIn('.on("click", ".js-order-row"', bind_handlers)
        self.assertIn('.on("click", ".js-print-order"', bind_handlers)
        # Reprinting a row must reuse the same printReceipt() as the main "Друк чека"
        # button, not a second copy of the print logic.
        self.assertIn("printReceipt(this.dataset.order)", bind_handlers)
        self.assertIn("function printReceipt(orderName = printableOrderName())", source)
        self.assertIn("function printReceiptBrowser(orderName = printableOrderName())", source)

        # Both the shift report and the daily report must render/bind chek rows through
        # this one shared implementation, not through separate copies.
        self.assertEqual(source.count("orderRowsHtml(report.orders || [])"), 2)
        self.assertEqual(source.count("bindOrderRowsHandlers(dialog);"), 2)

    def test_daily_report_covers_all_of_a_desks_shifts_for_the_day_by_payment_method(self):
        source = (APP / "ua_pos" / "page" / "ua_pos" / "ua_pos.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('async function showDailyReport()', source)
        self.assertIn('api("daily_report"', source)
        self.assertIn('$root.on("click", ".js-daily-report", showDailyReport);', source)

        api_source = (APP / "ua_pos" / "api.py").read_text(encoding="utf-8")
        daily_report = api_source[api_source.index("def daily_report(") :]
        daily_report = daily_report[: daily_report.index("\n@frappe.whitelist(", 1)]
        # Scoped to this cash desk, across every shift opened that calendar day (not just
        # the one currently open shift, unlike shift_report).
        self.assertIn('"cash_desk": session["cash_desk"]', daily_report)
        self.assertIn('"opened_at": ("between"', daily_report)
        self.assertIn("_payment_totals_by_method(order_names)", daily_report)
        self.assertIn("_attach_order_items(orders, order_names)", daily_report)

        # shift_report and daily_report must compute payment totals (net of returns) and
        # sales/returns totals through the same helpers, not duplicated SQL.
        self.assertEqual(api_source.count("_payment_totals_by_method(order_names)"), 2)
        self.assertEqual(api_source.count("_sales_and_returns_totals(orders)"), 2)

    def test_shift_report_api_returns_items_per_order_for_expansion(self):
        source = (APP / "ua_pos" / "api.py").read_text(encoding="utf-8")

        attach_items = source[source.index("def _attach_order_items(") :]
        attach_items = attach_items[: attach_items.index("\n\n\ndef ", 1)]
        self.assertIn("items_by_order = defaultdict(list)", attach_items)
        self.assertIn('order["items"] = items_by_order.get(order.name, [])', attach_items)

        shift_report = source[source.index("def shift_report(") :]
        shift_report = shift_report[: shift_report.index("\n@frappe.whitelist(", 1)]
        self.assertIn("_attach_order_items(orders, order_names)", shift_report)

    def test_non_fiscal_receipt_reprints_show_the_original_sale_time_not_now(self):
        print_service = (APP / "ua_pos" / "print_service.py").read_text(encoding="utf-8")
        render_order_receipt = print_service[print_service.index("def render_order_receipt(") :]
        render_order_receipt = render_order_receipt[: render_order_receipt.index("\n\ndef ", 1)]
        self.assertIn('format_datetime(order.modified, "dd.MM.yyyy HH:mm:ss")', render_order_receipt)
        self.assertNotIn("now_datetime(), align=", render_order_receipt)

        api_source = (APP / "ua_pos" / "api.py").read_text(encoding="utf-8")
        self.assertIn('"completed_at": str(doc.modified),', api_source)

        pos_js = (APP / "ua_pos" / "page" / "ua_pos" / "ua_pos.js").read_text(encoding="utf-8")
        self.assertIn("formatDateTime(data.completed_at)", pos_js)
        self.assertIn("Надруковано: ${esc(formatDateTime(data.printed_at))}", pos_js)

    def test_pos_cash_movement_has_a_denomination_count_child_table(self):
        doctype = json.loads(
            (
                APP
                / "ua_pos"
                / "doctype"
                / "pos_cash_movement"
                / "pos_cash_movement.json"
            ).read_text(encoding="utf-8")
        )
        field = next(f for f in doctype["fields"] if f["fieldname"] == "denomination_counts")
        self.assertEqual(field["fieldtype"], "Table")
        self.assertEqual(field["options"], "POS Denomination Count")
        self.assertIn("denomination_counts", doctype["field_order"])


if __name__ == "__main__":
    unittest.main()
