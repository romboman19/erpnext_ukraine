"""Ownership conversion stock, payable, tracking, FIFO and reversal acceptance."""

from __future__ import annotations

from decimal import Decimal

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from erpnext_ua.consignment_and_commission.api.v1 import (
    ownership_conversions as conversion_api,
)
from erpnext_ua.consignment_and_commission.integrations.candidates import (
    CCStockLotCandidateAdapter,
)
from erpnext_ua.consignment_and_commission.integrations.reconciliation import (
    audit_financial_integrity,
    get_partner_balances,
)
from erpnext_ua.consignment_and_commission.services.candidates import (
    CandidateQuery,
    preview_from_adapters,
)
from erpnext_ua.consignment_and_commission.services.ownership_conversion import (
    OwnershipConversionError,
)
from erpnext_ua.consignment_and_commission.services.stock_lot import (
    get_ownership_balance,
)
from erpnext_ua.consignment_and_commission.setup.ownership_dimension import (
    OWNERSHIP_CONVERSION_FIELD,
    OWNERSHIP_FIELD,
)

from .test_frappe_foundation import COMPANY, LOCATION, _cleanup_integration_records
from .test_frappe_own_receipt import _cleanup_own_receipts
from .test_frappe_receipt import (
    BATCH_ITEM,
    SERIAL_ITEM,
    _cleanup_off_balance_records,
    _cleanup_receipt_records,
    _enable_item_tracking,
    _ensure_item,
    _ensure_receipt_context,
)

SECURITY_OTHER_COMPANY = "_CC Security Other Company"


def _cleanup_security_other_company() -> None:
    frappe.set_user("Administrator")
    for permission in frappe.get_all(
        "User Permission",
        filters={"allow": "Company", "for_value": SECURITY_OTHER_COMPANY},
        pluck="name",
    ):
        frappe.delete_doc("User Permission", permission, force=True, ignore_permissions=True)
    if frappe.db.exists("Company", SECURITY_OTHER_COMPANY):
        frappe.delete_doc(
            "Company",
            SECURITY_OTHER_COMPANY,
            force=True,
            ignore_permissions=True,
        )


def _ensure_security_other_company() -> str:
    if frappe.db.exists("Company", SECURITY_OTHER_COMPANY):
        return SECURITY_OTHER_COMPANY
    return frappe.get_doc(
        {
            "doctype": "Company",
            "company_name": SECURITY_OTHER_COMPANY,
            "abbr": "CCO",
            "country": "United States",
            "default_currency": "USD",
            "create_chart_of_accounts_based_on": "Standard Template",
            "chart_of_accounts": "Standard",
        }
    ).insert(ignore_permissions=True).name


def _cleanup_conversions() -> None:
    if not frappe.db.exists("DocType", "CC Ownership Conversion"):
        return
    rows = frappe.get_all(
        "CC Ownership Conversion",
        filters={"company": COMPANY},
        fields=["name", "docstatus"],
    )
    for row in rows:
        document = frappe.get_doc("CC Ownership Conversion", row.name)
        if document.docstatus == 1:
            document.cancel()
    for row in rows:
        if frappe.db.exists("CC Ownership Conversion", row.name):
            frappe.delete_doc(
                "CC Ownership Conversion",
                row.name,
                force=True,
                ignore_permissions=True,
            )
    frappe.db.commit()


def _cleanup_all() -> None:
    frappe.set_user("Administrator")
    _cleanup_off_balance_records()
    _cleanup_conversions()
    _cleanup_own_receipts()
    _cleanup_receipt_records()
    _cleanup_integration_records()


def _delete_security_user(user: str) -> None:
    frappe.set_user("Administrator")
    for permission in frappe.get_all("User Permission", filters={"user": user}, pluck="name"):
        frappe.delete_doc("User Permission", permission, force=True, ignore_permissions=True)
    if frappe.db.exists("User", user):
        frappe.delete_doc("User", user, force=True, ignore_permissions=True)
    frappe.clear_cache(user=user)


def _create_security_user(user: str, role: str) -> None:
    _delete_security_user(user)
    frappe.get_doc(
        {
            "doctype": "User",
            "email": user,
            "first_name": "CC Security",
            "enabled": 1,
            "send_welcome_email": 0,
            "roles": [{"role": role}],
        }
    ).insert(ignore_permissions=True)
    frappe.clear_cache(user=user)


def _enable_conversion_methods() -> None:
    settings = frappe.get_single("CC Settings")
    settings.enable_buyout = 1
    settings.enable_deferred_purchase = 1
    settings.save(ignore_permissions=True)


class TestFrappeOwnershipConversion(IntegrationTestCase):
    def test_api_enforces_roles_lifecycle_permission_and_company_scope(self) -> None:
        _cleanup_all()
        operator = "_cc.security.operator@example.com"
        auditor = "_cc.security.auditor@example.com"
        self.addCleanup(_delete_security_user, auditor)
        self.addCleanup(_delete_security_user, operator)
        self.addCleanup(_cleanup_all)
        company, _warehouses, contract = _ensure_receipt_context()
        _enable_conversion_methods()
        item_code = _ensure_item()
        receipt = frappe.get_doc(
            {
                "doctype": "CC Receipt",
                "posting_date": nowdate(),
                "posting_time": "00:00:10",
                "contract": contract,
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 1,
                        "uom": "Nos",
                        "accounting_unit_value": 100,
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        receipt.submit()
        source_lot = receipt.items[0].stock_lot
        arguments = {
            "idempotency_key": f"conversion-security-{frappe.generate_hash(length=10)}",
            "posting_date": nowdate(),
            "posting_time": "00:00:11",
            "source_lot": source_lot,
            "qty": "1",
            "source_method": "BUYOUT",
            "unit_cost": "25",
            "currency": company.default_currency,
            "exchange_rate": "1",
            "reason": "Permission acceptance",
        }
        _create_security_user(auditor, "Commission Trade Auditor")
        frappe.set_user(auditor)
        with self.assertRaises(frappe.PermissionError):
            conversion_api.create(**arguments)

        _create_security_user(operator, "Commission Trade User")
        other_company = _ensure_security_other_company()
        self.addCleanup(_cleanup_security_other_company)
        frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": operator,
                "allow": "Company",
                "for_value": other_company,
                "apply_to_all_doctypes": 1,
            }
        ).insert(ignore_permissions=True)
        frappe.clear_cache(user=operator)
        frappe.set_user(operator)
        with self.assertRaises(frappe.PermissionError):
            conversion_api.create(**arguments)

        frappe.set_user("Administrator")
        for permission in frappe.get_all(
            "User Permission",
            filters={"user": operator},
            pluck="name",
        ):
            frappe.delete_doc("User Permission", permission, force=True, ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": operator,
                "allow": "Company",
                "for_value": company.name,
                "apply_to_all_doctypes": 1,
            }
        ).insert(ignore_permissions=True)
        frappe.clear_cache(user=operator)
        frappe.set_user(operator)
        created = conversion_api.create(**arguments)
        with self.assertRaises(frappe.PermissionError):
            conversion_api.submit(ownership_conversion=created["name"])

        frappe.set_user("Administrator")
        submitted = conversion_api.submit(ownership_conversion=created["name"])
        self.assertEqual(submitted["status"], "CONVERTED")
        conversion_api.cancel(ownership_conversion=created["name"])

    def test_api_conversion_posts_asset_payable_and_rejoins_global_fifo(self) -> None:
        _cleanup_all()
        self.addCleanup(_cleanup_all)
        company, warehouses, contract = _ensure_receipt_context()
        _enable_conversion_methods()
        item_code = _ensure_item()
        receipt = frappe.get_doc(
            {
                "doctype": "CC Receipt",
                "posting_date": nowdate(),
                "posting_time": "00:01:00",
                "contract": contract,
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 2,
                        "uom": "Nos",
                        "accounting_unit_value": 100,
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        receipt.submit()
        source_lot = receipt.items[0].stock_lot
        key = f"conversion-api-{frappe.generate_hash(length=12)}"
        arguments = {
            "idempotency_key": key,
            "posting_date": nowdate(),
            "posting_time": "00:02:00",
            "source_lot": source_lot,
            "qty": "1.0",
            "source_method": "BUYOUT",
            "unit_cost": "80.00",
            "currency": company.default_currency,
            "exchange_rate": "1",
            "reason": "Company accepted one unsold unit",
        }
        first = conversion_api.create(**arguments)
        replay = conversion_api.create(**{**arguments, "qty": "1"})
        self.assertEqual(first["name"], replay["name"])
        with self.assertRaisesRegex(OwnershipConversionError, "belongs to another request"):
            conversion_api.create(**{**arguments, "unit_cost": "81"})

        posted = conversion_api.submit(ownership_conversion=first["name"])
        self.assertEqual(posted["status"], "CONVERTED")
        conversion = frappe.get_doc("CC Ownership Conversion", first["name"])
        self.assertEqual(Decimal(str(conversion.off_balance_amount)), Decimal("100.0"))
        account_024 = frappe.get_doc(
            "UA Off Balance Entry",
            conversion.off_balance_entry,
        )
        self.assertEqual(account_024.docstatus, 1)
        self.assertEqual(account_024.direction, "Decrease")
        self.assertEqual(Decimal(str(account_024.quantity)), Decimal("1.0"))
        self.assertEqual(Decimal(str(account_024.amount)), Decimal("100.0"))
        self.assertEqual(account_024.reference_doctype, "CC Ownership Conversion")
        self.assertEqual(account_024.reference_name, conversion.name)
        self.assertEqual(get_ownership_balance(source_lot), Decimal("1.0"))
        self.assertEqual(get_ownership_balance(posted["target_lot"]), Decimal("1.0"))
        target_lot = frappe.get_doc("CC Stock Lot", posted["target_lot"])
        self.assertEqual(target_lot.relationship_model, "OWN")
        self.assertEqual(target_lot.source_method, "BUYOUT")
        self.assertEqual(target_lot.ownership_conversion, first["name"])

        issue = frappe.get_doc("Stock Entry", posted["source_issue"])
        self.assertEqual(issue.get(OWNERSHIP_CONVERSION_FIELD), first["name"])
        self.assertFalse(
            frappe.db.exists(
                "GL Entry",
                {"voucher_type": "Stock Entry", "voucher_no": issue.name, "is_cancelled": 0},
            )
        )
        invoice = frappe.get_doc("Purchase Invoice", posted["purchase_invoice"])
        self.assertEqual(invoice.get(OWNERSHIP_CONVERSION_FIELD), first["name"])
        self.assertEqual(Decimal(str(invoice.outstanding_amount)), Decimal("80.0"))
        active_gl = frappe.get_all(
            "GL Entry",
            filters={
                "voucher_type": "Purchase Invoice",
                "voucher_no": invoice.name,
                "is_cancelled": 0,
            },
            fields=["debit", "credit"],
        )
        self.assertEqual(sum(Decimal(str(row.debit)) for row in active_gl), Decimal("80.0"))
        self.assertEqual(sum(Decimal(str(row.credit)) for row in active_gl), Decimal("80.0"))
        obligation = next(
            row
            for row in get_partner_balances({"company": company.name})
            if row["relationship_model"] == "OWN/BUYOUT"
        )
        self.assertEqual(obligation["contract"], receipt.contract)
        self.assertEqual(obligation["reported_amount"], Decimal("80.0"))
        self.assertEqual(obligation["outstanding_amount"], Decimal("80.0"))
        self.assertEqual(obligation["purchase_invoices"], 1)
        audit = audit_financial_integrity(company=company.name)
        self.assertTrue(audit["ok"], audit["issues"])
        self.assertEqual(audit["checked"]["ownership_conversions"], 1)

        preview = preview_from_adapters(
            [CCStockLotCandidateAdapter()],
            query=CandidateQuery(
                item_code=item_code,
                company=company.name,
                location=LOCATION,
                allowed_warehouses=frozenset(
                    {warehouses["COMMISSION"], warehouses["OWN"]}
                ),
            ),
            qty=Decimal("2"),
        )
        self.assertEqual([row.lot_name for row in preview], [source_lot, target_lot.name])
        self.assertEqual([row.source_method for row in preview], ["COMMISSION", "BUYOUT"])
        with self.assertRaisesRegex(frappe.ValidationError, "Cancel linked CC Ownership Conversion"):
            issue.cancel()
        with self.assertRaisesRegex(frappe.ValidationError, "Cancel linked CC Ownership Conversion"):
            frappe.get_doc("CC Own Receipt", posted["own_receipt"]).cancel()
        with self.assertRaisesRegex(frappe.ValidationError, "Cancel submitted CC Ownership"):
            receipt.cancel()

        cancelled = conversion_api.cancel(ownership_conversion=first["name"])
        self.assertEqual(cancelled["status"], "CANCELLED")
        account_024.reload()
        self.assertEqual(account_024.docstatus, 2)
        self.assertEqual(get_ownership_balance(source_lot), Decimal("2.0"))
        self.assertEqual(get_ownership_balance(target_lot.name), Decimal("0.0"))
        self.assertFalse(
            any(
                row["relationship_model"] == "OWN/BUYOUT"
                for row in get_partner_balances({"company": company.name})
            )
        )

    def test_serial_and_batch_conversion_preserve_audited_identity_mapping(self) -> None:
        _cleanup_all()
        self.addCleanup(_cleanup_all)
        _enable_item_tracking()
        company, warehouses, contract = _ensure_receipt_context()
        _enable_conversion_methods()
        serial_item = _ensure_item(SERIAL_ITEM, has_serial_no=True)
        batch_item = _ensure_item(BATCH_ITEM, has_batch_no=True)
        serials = ("_CC-CONVERT-SERIAL-1", "_CC-CONVERT-SERIAL-2")
        receipt = frappe.get_doc(
            {
                "doctype": "CC Receipt",
                "posting_date": nowdate(),
                "posting_time": "00:03:00",
                "contract": contract,
                "items": [
                    {
                        "item_code": serial_item,
                        "qty": 2,
                        "uom": "Nos",
                        "accounting_unit_value": 100,
                        "serial_numbers": "\n".join(serials),
                    },
                    {
                        "item_code": batch_item,
                        "qty": 2,
                        "uom": "Nos",
                        "accounting_unit_value": 100,
                    },
                ],
            }
        ).insert(ignore_permissions=True)
        receipt.submit()
        receipt.reload()
        serial_line, batch_line = receipt.items
        target_batch = f"_CC-CONVERT-BATCH-{frappe.generate_hash(length=8)}"

        serial_conversion = frappe.get_doc(
            {
                "doctype": "CC Ownership Conversion",
                "posting_date": nowdate(),
                "posting_time": "00:04:00",
                "source_lot": serial_line.stock_lot,
                "qty": 1,
                "source_method": "BUYOUT",
                "unit_cost": 110,
                "currency": company.default_currency,
                "exchange_rate": 1,
                "reason": "Convert one serialized unit",
                "serial_numbers": serials[0],
            }
        ).insert(ignore_permissions=True)
        batch_conversion = frappe.get_doc(
            {
                "doctype": "CC Ownership Conversion",
                "posting_date": nowdate(),
                "posting_time": "00:05:00",
                "source_lot": batch_line.stock_lot,
                "qty": 1,
                "source_method": "BUYOUT",
                "unit_cost": 70,
                "currency": company.default_currency,
                "exchange_rate": 1,
                "reason": "Convert one batch-tracked unit",
                "target_batch_no": target_batch,
            }
        ).insert(ignore_permissions=True)
        serial_conversion.submit()
        batch_conversion.submit()
        serial_conversion.reload()
        batch_conversion.reload()

        self.assertEqual(
            frappe.db.get_value("Serial No", serials[0], OWNERSHIP_FIELD),
            serial_conversion.target_lot,
        )
        self.assertEqual(
            frappe.db.get_value("Serial No", serials[0], "warehouse"),
            warehouses["OWN"],
        )
        self.assertEqual(
            frappe.db.get_value("Serial No", serials[1], OWNERSHIP_FIELD),
            serial_line.stock_lot,
        )
        self.assertEqual(
            frappe.db.get_value("Batch", batch_line.batch_no, OWNERSHIP_FIELD),
            batch_line.stock_lot,
        )
        self.assertEqual(
            frappe.db.get_value("Batch", target_batch, OWNERSHIP_FIELD),
            batch_conversion.target_lot,
        )
        self.assertEqual(get_ownership_balance(serial_line.stock_lot), Decimal("1.0"))
        self.assertEqual(get_ownership_balance(batch_line.stock_lot), Decimal("1.0"))
        self.assertEqual(get_ownership_balance(serial_conversion.target_lot), Decimal("1.0"))
        self.assertEqual(get_ownership_balance(batch_conversion.target_lot), Decimal("1.0"))

        batch_conversion.cancel()
        serial_conversion.cancel()
        self.assertEqual(get_ownership_balance(serial_line.stock_lot), Decimal("2.0"))
        self.assertEqual(get_ownership_balance(batch_line.stock_lot), Decimal("2.0"))
        self.assertEqual(
            frappe.db.get_value("Serial No", serials[0], OWNERSHIP_FIELD),
            serial_line.stock_lot,
        )
        self.assertEqual(
            {frappe.db.get_value("Serial No", serial_no, "warehouse") for serial_no in serials},
            {warehouses["COMMISSION"]},
        )
