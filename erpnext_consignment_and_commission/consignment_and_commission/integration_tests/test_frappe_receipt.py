"""Clean-site Stage 2 receipt, ownership lot and Stock Entry integration."""

from __future__ import annotations

from decimal import Decimal

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import getdate, nowdate

from erpnext_consignment_and_commission.consignment_and_commission.constants import STOCK_DOCTYPES
from erpnext_consignment_and_commission.consignment_and_commission.integrations.candidates import (
    CCStockLotCandidateAdapter,
)
from erpnext_consignment_and_commission.consignment_and_commission.services.candidates import (
    CandidateQuery,
    preview_from_adapters,
)
from erpnext_consignment_and_commission.consignment_and_commission.services.stock_lot import (
    get_ownership_balance,
)
from erpnext_consignment_and_commission.consignment_and_commission.setup.ownership_dimension import (
    BALANCE_INDEX,
    DIMENSION_NAME,
    OWNERSHIP_FIELD,
    RECEIPT_FIELD,
)

from .test_frappe_foundation import (
    COMPANY,
    LOCATION,
    PARTNER,
    _cleanup_integration_records,
    _enable_multiple_item_transactions,
    _ensure_company,
    _ensure_company_dependencies,
    _ensure_supplier,
    _ensure_warehouses,
)

ITEM = "_CC Integration Receipt Item"
BATCH_ITEM = "_CC Integration Batch Receipt Item"
SERIAL_ITEM = "_CC Integration Serial Receipt Item"
ITEM_GROUP = "_CC Integration Items"
ITEM_GROUP_ROOT = "_CC Integration All Item Groups"
CONTRACT_TITLE = "_CC Integration Receipt Contract"
BATCH_SERIES = "_CC-BATCH-.#####"
SERIAL_SERIES = "_CC-SERIAL-.#####"
_created_item_group_root = False
_created_stock_entry_types: set[str] = set()
_created_uom = False
_original_tracking_setting: int | None = None


def _cancel_stock_entry_for_cleanup(stock_entry_name: str) -> None:
    stock_entry = frappe.get_doc("Stock Entry", stock_entry_name)
    if stock_entry.docstatus != 1:
        return
    previous = getattr(frappe.flags, "cc_receipt_cancellation", False)
    frappe.flags.cc_receipt_cancellation = True
    try:
        stock_entry.cancel()
    finally:
        frappe.flags.cc_receipt_cancellation = previous


def _cleanup_off_balance_records() -> None:
    """Purge account-024 audit documents only inside the ephemeral test site."""
    if not frappe.db.exists("DocType", "UA Off Balance Entry"):
        return
    entries = frappe.get_all(
        "UA Off Balance Entry",
        filters={"company": COMPANY},
        fields=["name", "direction", "reference_doctype"],
        order_by="creation desc",
    )
    cancellation_groups = (
        [row for row in entries if row.direction == "Decrease"],
        [
            row
            for row in entries
            if row.direction == "Increase" and row.reference_doctype != "CC Receipt"
        ],
        [
            row
            for row in entries
            if row.direction == "Increase" and row.reference_doctype == "CC Receipt"
        ],
    )
    for group in cancellation_groups:
        for row in group:
            entry = frappe.get_doc("UA Off Balance Entry", row.name)
            if entry.docstatus == 1:
                ignored = set(entry.get("ignore_linked_doctypes") or ())
                if row.reference_doctype:
                    ignored.add(row.reference_doctype)
                entry.ignore_linked_doctypes = tuple(sorted(ignored))
                entry.cancel()

    receipt_names = frappe.get_all(
        "CC Receipt",
        filters={"company": COMPANY},
        pluck="name",
    )
    for receipt_name in receipt_names:
        for row_name in frappe.get_all(
            "CC Receipt Item",
            filters={"parent": receipt_name},
            pluck="name",
        ):
            frappe.db.set_value(
                "CC Receipt Item",
                row_name,
                "off_balance_entry",
                None,
                update_modified=False,
            )
    for doctype in (
        "CC Stock Lot",
        "CC Sale Allocation",
        "CC Sale Return Allocation",
        "CC Partner Return",
        "CC Ownership Conversion",
    ):
        if not frappe.db.exists("DocType", doctype):
            continue
        for name in frappe.get_all(doctype, filters={"company": COMPANY}, pluck="name"):
            frappe.db.set_value(
                doctype,
                name,
                "off_balance_entry",
                None,
                update_modified=False,
            )
    for row in entries:
        if frappe.db.exists("UA Off Balance Entry", row.name):
            frappe.delete_doc(
                "UA Off Balance Entry",
                row.name,
                force=True,
                ignore_permissions=True,
            )


def _cleanup_receipt_records() -> None:
    global _created_item_group_root, _created_stock_entry_types, _created_uom, _original_tracking_setting

    _cleanup_off_balance_records()
    receipt_names = frappe.get_all("CC Receipt", filters={"company": COMPANY}, pluck="name")
    linked_entries = set(
        frappe.get_all("Stock Entry", filters={RECEIPT_FIELD: ("in", receipt_names)}, pluck="name")
        if receipt_names
        else []
    )
    linked_entries.update(
        frappe.get_all(
            "Stock Entry Detail",
            filters={"item_code": ("in", [ITEM, BATCH_ITEM, SERIAL_ITEM])},
            pluck="parent",
        )
    )
    for receipt_name in receipt_names:
        receipt = frappe.get_doc("CC Receipt", receipt_name)
        if receipt.stock_entry:
            linked_entries.add(receipt.stock_entry)
        if receipt.docstatus == 1 and receipt.stock_entry:
            receipt.cancel()
        elif receipt.docstatus == 1:
            frappe.db.set_value("CC Receipt", receipt.name, "docstatus", 2, update_modified=False)

    for stock_entry_name in linked_entries:
        if frappe.db.exists("Stock Entry", stock_entry_name):
            _cancel_stock_entry_for_cleanup(stock_entry_name)

    for receipt_name in receipt_names:
        for row_name in frappe.get_all("CC Receipt Item", filters={"parent": receipt_name}, pluck="name"):
            frappe.db.set_value(
                "CC Receipt Item",
                row_name,
                {"stock_lot": None, "stock_entry_detail": None},
                update_modified=False,
            )
        frappe.db.set_value("CC Receipt", receipt_name, "stock_entry", None, update_modified=False)
    for lot_name in frappe.get_all("CC Stock Lot", filters={"company": COMPANY}, pluck="name"):
        frappe.db.set_value(
            "CC Stock Lot",
            lot_name,
            {"stock_entry": None, "stock_entry_detail": None},
            update_modified=False,
        )

    lot_names = frappe.get_all("CC Stock Lot", filters={"company": COMPANY}, pluck="name")
    if lot_names:
        for doctype in ("Batch", "Serial No"):
            for tracking_name in frappe.get_all(
                doctype,
                filters={OWNERSHIP_FIELD: ("in", lot_names)},
                pluck="name",
            ):
                frappe.db.set_value(
                    doctype,
                    tracking_name,
                    OWNERSHIP_FIELD,
                    None,
                    update_modified=False,
                )

    for stock_entry_name in linked_entries:
        if frappe.db.exists("Stock Entry", stock_entry_name):
            frappe.delete_doc("Stock Entry", stock_entry_name, force=True, ignore_permissions=True)
    for lot_name in frappe.get_all("CC Stock Lot", filters={"company": COMPANY}, pluck="name"):
        frappe.delete_doc("CC Stock Lot", lot_name, force=True, ignore_permissions=True)
    for receipt_name in receipt_names:
        if frappe.db.exists("CC Receipt", receipt_name):
            frappe.delete_doc("CC Receipt", receipt_name, force=True, ignore_permissions=True)

    for stock_entry_type in tuple(_created_stock_entry_types):
        if frappe.db.exists("Stock Entry Type", stock_entry_type):
            frappe.delete_doc("Stock Entry Type", stock_entry_type, force=True, ignore_permissions=True)
        _created_stock_entry_types.discard(stock_entry_type)
    for item_code in (ITEM, BATCH_ITEM, SERIAL_ITEM):
        for doctype, item_field in (("Serial No", "item_code"), ("Batch", "item")):
            for tracking_name in frappe.get_all(
                doctype,
                filters={item_field: item_code},
                pluck="name",
            ):
                frappe.delete_doc(doctype, tracking_name, force=True, ignore_permissions=True)
        if frappe.db.exists("Item", item_code):
            frappe.delete_doc("Item", item_code, force=True, ignore_permissions=True)
    if frappe.db.exists("Item Group", ITEM_GROUP):
        frappe.delete_doc("Item Group", ITEM_GROUP, force=True, ignore_permissions=True)
    if _created_item_group_root and frappe.db.exists("Item Group", ITEM_GROUP_ROOT):
        frappe.delete_doc("Item Group", ITEM_GROUP_ROOT, force=True, ignore_permissions=True)
        _created_item_group_root = False
    if _created_uom and frappe.db.exists("UOM", "Nos"):
        frappe.delete_doc("UOM", "Nos", force=True, ignore_permissions=True)
        _created_uom = False
    if _original_tracking_setting is not None:
        frappe.db.set_single_value(
            "Stock Settings",
            "enable_serial_and_batch_no_for_item",
            _original_tracking_setting,
        )
        _original_tracking_setting = None
    frappe.db.commit()
    frappe.clear_cache()


def _ensure_item(
    item_code: str = ITEM,
    *,
    has_batch_no: bool = False,
    has_serial_no: bool = False,
) -> str:
    global _created_item_group_root, _created_uom

    if not frappe.db.exists("UOM", "Nos"):
        frappe.get_doc({"doctype": "UOM", "uom_name": "Nos"}).insert(ignore_permissions=True)
        _created_uom = True

    if frappe.db.exists("Item", item_code):
        return item_code

    root_rows = frappe.get_all(
        "Item Group",
        filters={"is_group": 1},
        fields=["name"],
        order_by="lft asc",
        limit=1,
    )
    if not root_rows:
        root = frappe.get_doc(
            {
                "doctype": "Item Group",
                "item_group_name": ITEM_GROUP_ROOT,
                "is_group": 1,
            }
        ).insert(ignore_permissions=True)
        root_rows = [frappe._dict(name=root.name)]
        _created_item_group_root = True

    item_group = (
        frappe.get_doc("Item Group", ITEM_GROUP)
        if frappe.db.exists("Item Group", ITEM_GROUP)
        else frappe.get_doc(
            {
                "doctype": "Item Group",
                "item_group_name": ITEM_GROUP,
                "parent_item_group": root_rows[0].name,
                "is_group": 0,
            }
        ).insert(ignore_permissions=True)
    )
    return frappe.get_doc(
        {
            "doctype": "Item",
            "item_code": item_code,
            "item_name": item_code,
            "item_group": item_group.name,
            "stock_uom": "Nos",
            "is_stock_item": 1,
            "valuation_method": "FIFO",
            "has_batch_no": int(has_batch_no),
            "create_new_batch": int(has_batch_no),
            "batch_number_series": BATCH_SERIES if has_batch_no else None,
            "has_serial_no": int(has_serial_no),
            "serial_no_series": SERIAL_SERIES if has_serial_no else None,
        }
    ).insert(ignore_permissions=True).name


def _ensure_stock_entry_types() -> None:
    for purpose in ("Material Receipt", "Material Issue", "Material Transfer"):
        if frappe.db.exists("Stock Entry Type", {"purpose": purpose}):
            continue
        stock_entry_type = frappe.get_doc(
            {
                "doctype": "Stock Entry Type",
                "__newname": purpose,
                "purpose": purpose,
                "is_standard": 1,
            }
        ).insert(ignore_permissions=True)
        _created_stock_entry_types.add(stock_entry_type.name)


def _enable_item_tracking() -> None:
    global _original_tracking_setting

    if _original_tracking_setting is None:
        _original_tracking_setting = int(
            frappe.db.get_single_value(
                "Stock Settings",
                "enable_serial_and_batch_no_for_item",
            )
            or 0
        )
    frappe.db.set_single_value(
        "Stock Settings",
        "enable_serial_and_batch_no_for_item",
        1,
    )
    frappe.clear_cache(doctype="Stock Settings")


def _ensure_fiscal_year(posting_date: str) -> str:
    from erpnext.accounts.utils import FiscalYearError, get_fiscal_year

    try:
        return get_fiscal_year(posting_date, company=COMPANY)[0]
    except FiscalYearError:
        date = getdate(posting_date)
        fiscal_year_name = f"_CC Integration Fiscal Year {date.year}"
        if frappe.db.exists("Fiscal Year", fiscal_year_name):
            fiscal_year = frappe.get_doc("Fiscal Year", fiscal_year_name)
            if not frappe.db.exists(
                "Fiscal Year Company",
                {"parent": fiscal_year_name, "company": COMPANY},
            ):
                fiscal_year.append("companies", {"company": COMPANY})
                fiscal_year.save(ignore_permissions=True)
        else:
            frappe.get_doc(
                {
                    "doctype": "Fiscal Year",
                    "year": fiscal_year_name,
                    "year_start_date": f"{date.year}-01-01",
                    "year_end_date": f"{date.year}-12-31",
                    "companies": [{"company": COMPANY}],
                }
            ).insert(ignore_permissions=True)
        frappe.clear_cache()
        return fiscal_year_name


def _ensure_receipt_context() -> tuple[frappe.model.document.Document, dict[str, str], str]:
    _enable_multiple_item_transactions()
    _ensure_company_dependencies()
    company = _ensure_company()
    from erpnext_consignment_and_commission.consignment_and_commission.spikes.accounting import (
        _ensure_accounts,
    )

    accounts = _ensure_accounts(frappe, company.name, require_payment_accounts=False)
    mapping_values = {
        "off_balance_goods_account": accounts["off_balance_goods"],
        "gross_proceeds_clearing_account": accounts["commission_gross_proceeds"],
        "commission_revenue_account": accounts["commission_revenue"],
        "principal_proceeds_deduction_account": accounts["principal_proceeds_deduction"],
        "unreported_commission_liability_account": accounts[
            "unreported_commission_liability"
        ],
        "unreported_consignment_liability_account": accounts[
            "unreported_consignment_liability"
        ],
        "default_supplier_payable_account": accounts["supplier_payable"],
    }
    if frappe.db.exists("CC Account Mapping", company.name):
        mapping = frappe.get_doc("CC Account Mapping", company.name)
        mapping.update(mapping_values)
        mapping.save(ignore_permissions=True)
    else:
        frappe.get_doc(
            {
                "doctype": "CC Account Mapping",
                "company": company.name,
                **mapping_values,
            }
        ).insert(ignore_permissions=True)
    _ensure_fiscal_year(nowdate())
    warehouses = _ensure_warehouses()
    supplier = _ensure_supplier()
    _ensure_stock_entry_types()

    location = frappe.get_doc(
        {
            "doctype": "CC Location",
            "location_name": LOCATION,
            "company": COMPANY,
            "legal_entity_type": "Company",
            "legal_entity_name": COMPANY,
            "own_warehouse": warehouses["OWN"],
            "commission_warehouse": warehouses["COMMISSION"],
            "consignment_warehouse": warehouses["CONSIGNMENT"],
        }
    ).insert(ignore_permissions=True)
    partner = frappe.get_doc(
        {
            "doctype": "CC Partner Profile",
            "partner_name": PARTNER,
            "supplier": supplier.name,
            "allowed_relationship_models": "BOTH",
            "default_currency": company.default_currency,
            "default_settlement_deadline_days": 7,
        }
    ).insert(ignore_permissions=True)
    contract = frappe.get_doc(
        {
            "doctype": "CC Contract",
            "contract_title": CONTRACT_TITLE,
            "status": "ACTIVE",
            "partner_profile": partner.name,
            "company": COMPANY,
            "location": location.name,
            "relationship_model": "COMMISSION",
            "currency": company.default_currency,
            "commission_rate": 15,
            "valid_from": nowdate(),
            "settlement_frequency": "MONTHLY",
            "settlement_deadline_days": 7,
            "fiscal_policy": "AUTO",
            "price_authority": "COMPANY",
        }
    ).insert(ignore_permissions=True)

    settings = frappe.get_single("CC Settings")
    settings.update(
        {
            "enabled": 1,
            "enable_commission": 1,
            "enable_consignment": 1,
            "default_company": COMPANY,
            "default_location": location.name,
            "reservation_ttl_minutes": 15,
            "allocation_retry_limit": 3,
        }
    )
    settings.save(ignore_permissions=True)
    return company, warehouses, contract.name


class TestFrappeReceipt(IntegrationTestCase):
    def test_zero_value_receipt_lot_balance_cancel_and_direct_cancel_guard(self) -> None:
        _cleanup_receipt_records()
        _cleanup_integration_records()
        self.addCleanup(_cleanup_integration_records)
        self.addCleanup(_cleanup_receipt_records)

        for doctype in STOCK_DOCTYPES:
            self.assertTrue(frappe.db.exists("DocType", doctype), doctype)
        self.assertTrue(frappe.db.exists("Inventory Dimension", DIMENSION_NAME))
        self.assertTrue(frappe.db.has_index("tabStock Ledger Entry", BALANCE_INDEX))
        self.assertTrue(frappe.get_meta("Stock Entry Detail").has_field(f"to_{OWNERSHIP_FIELD}"))
        self.assertTrue(frappe.get_meta("Batch").has_field(OWNERSHIP_FIELD))
        self.assertTrue(frappe.get_meta("Serial No").has_field(OWNERSHIP_FIELD))

        company, warehouses, contract_name = _ensure_receipt_context()
        item_code = _ensure_item()

        receipt = frappe.get_doc(
            {
                "doctype": "CC Receipt",
                "posting_date": nowdate(),
                "contract": contract_name,
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 3,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "accounting_unit_value": 100,
                    },
                    {
                        "item_code": item_code,
                        "qty": 2,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "accounting_unit_value": 100,
                    },
                ],
            }
        ).insert(ignore_permissions=True)
        receipt.submit()
        receipt.reload()

        self.assertEqual(receipt.docstatus, 1)
        self.assertEqual(receipt.warehouse, warehouses["COMMISSION"])
        self.assertEqual(receipt.total_stock_qty, 5)
        self.assertTrue(receipt.stock_entry)
        stock_entry = frappe.get_doc("Stock Entry", receipt.stock_entry)
        self.assertEqual(stock_entry.docstatus, 1)
        self.assertEqual(stock_entry.get(RECEIPT_FIELD), receipt.name)

        lots = [frappe.get_doc("CC Stock Lot", line.stock_lot) for line in receipt.items]
        self.assertEqual(len({lot.name for lot in lots}), 2)
        for line, lot in zip(receipt.items, lots, strict=True):
            self.assertEqual(lot.lot_status, "OPEN")
            self.assertEqual(lot.received_qty, line.stock_qty)
            self.assertEqual(lot.relationship_model, "COMMISSION")
            self.assertEqual(lot.source_method, "COMMISSION")
            self.assertEqual(lot.warehouse, warehouses["COMMISSION"])
            self.assertEqual(get_ownership_balance(lot.name), line.stock_qty)
            self.assertEqual(Decimal(str(line.accounting_amount)), Decimal(str(line.stock_qty)) * 100)
            self.assertTrue(line.off_balance_entry)
            self.assertTrue(lot.off_balance_account)
            self.assertEqual(Decimal(str(lot.off_balance_amount)), Decimal(str(line.accounting_amount)))
            self.assertEqual(lot.off_balance_entry, line.off_balance_entry)

        account_024_entries = frappe.get_all(
            "UA Off Balance Entry",
            filters={
                "reference_doctype": "CC Receipt",
                "reference_name": receipt.name,
                "docstatus": 1,
            },
            fields=["direction", "quantity", "amount", "off_balance_account"],
        )
        self.assertEqual(len(account_024_entries), 2)
        self.assertEqual({row.direction for row in account_024_entries}, {"Increase"})
        self.assertEqual(
            sum(Decimal(str(row.quantity)) for row in account_024_entries),
            Decimal("5.0"),
        )
        self.assertEqual(
            sum(Decimal(str(row.amount)) for row in account_024_entries),
            Decimal("500.0"),
        )
        self.assertEqual(
            {row.off_balance_account for row in account_024_entries},
            {lot.off_balance_account for lot in lots},
        )

        preview = preview_from_adapters(
            [CCStockLotCandidateAdapter()],
            query=CandidateQuery(
                item_code=item_code,
                company=company.name,
                location=receipt.location,
                allowed_warehouses=frozenset({warehouses["COMMISSION"]}),
            ),
            qty=Decimal("4"),
        )
        self.assertEqual([row.lot_name for row in preview], [lots[0].name, lots[1].name])
        self.assertEqual([row.qty for row in preview], [Decimal("3"), Decimal("1")])
        self.assertEqual([row.source_method for row in preview], ["COMMISSION", "COMMISSION"])

        ledger = frappe.get_all(
            "Stock Ledger Entry",
            filters={"voucher_no": stock_entry.name, "is_cancelled": 0},
            fields=["actual_qty", "valuation_rate", "stock_value_difference", OWNERSHIP_FIELD],
        )
        self.assertEqual(len(ledger), 2)
        self.assertEqual(sorted(row.actual_qty for row in ledger), [2, 3])
        self.assertEqual({row.get(OWNERSHIP_FIELD) for row in ledger}, {lot.name for lot in lots})
        for row in ledger:
            self.assertEqual(row.valuation_rate, 0)
            self.assertEqual(row.stock_value_difference, 0)

        with self.assertRaisesRegex(frappe.ValidationError, "Cancel linked CC Receipt"):
            stock_entry.cancel()
        stock_entry.reload()
        self.assertEqual(stock_entry.docstatus, 1)

        receipt.cancel()
        stock_entry.reload()
        self.assertEqual(receipt.docstatus, 2)
        self.assertEqual(stock_entry.docstatus, 2)
        for lot in lots:
            lot.reload()
            self.assertEqual(lot.lot_status, "CANCELLED")
            self.assertEqual(get_ownership_balance(lot.name), 0)
        self.assertFalse(
            frappe.db.exists(
                "UA Off Balance Entry",
                {
                    "reference_doctype": "CC Receipt",
                    "reference_name": receipt.name,
                    "docstatus": 1,
                },
            )
        )

    def test_tracked_receipt_owns_masters_and_rejects_cross_owner_issue(self) -> None:
        from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

        _cleanup_receipt_records()
        _cleanup_integration_records()
        self.addCleanup(_cleanup_integration_records)
        self.addCleanup(_cleanup_receipt_records)

        company, warehouses, contract_name = _ensure_receipt_context()
        _enable_item_tracking()
        batch_item = _ensure_item(BATCH_ITEM, has_batch_no=True)
        serial_item = _ensure_item(SERIAL_ITEM, has_serial_no=True)
        explicit_serials = ("_CC-EXPLICIT-SERIAL-001", "_CC-EXPLICIT-SERIAL-002")

        native_receipt = make_stock_entry(
            item_code=batch_item,
            company=company.name,
            to_warehouse=warehouses["OWN"],
            qty=1,
            rate=1,
            use_serial_batch_fields=1,
        )
        native_batch = frappe.db.get_value(
            "Serial and Batch Entry",
            {"parent": native_receipt.items[0].serial_and_batch_bundle},
            "batch_no",
        )
        self.assertTrue(native_batch)
        self.assertFalse(frappe.db.get_value("Batch", native_batch, OWNERSHIP_FIELD))
        native_receipt.cancel()

        receipt = frappe.get_doc(
            {
                "doctype": "CC Receipt",
                "posting_date": nowdate(),
                "contract": contract_name,
                "items": [
                    {
                        "item_code": batch_item,
                        "qty": 2,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "accounting_unit_value": 100,
                    },
                    {
                        "item_code": serial_item,
                        "qty": 2,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "accounting_unit_value": 100,
                        "serial_numbers": "\n".join(explicit_serials),
                    },
                    {
                        "item_code": serial_item,
                        "qty": 1,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "accounting_unit_value": 100,
                    },
                ],
            }
        ).insert(ignore_permissions=True)
        receipt.submit()
        receipt.reload()

        batch_line, explicit_line, automatic_line = receipt.items
        self.assertEqual(batch_line.tracking_type, "BATCH")
        self.assertTrue(batch_line.batch_no)
        self.assertEqual(explicit_line.tracking_type, "SERIAL")
        self.assertEqual(tuple(explicit_line.serial_numbers.splitlines()), explicit_serials)
        self.assertEqual(automatic_line.tracking_type, "SERIAL")
        self.assertEqual(len(automatic_line.serial_numbers.splitlines()), 1)

        tracked_lines = (batch_line, explicit_line, automatic_line)
        tracked_masters = [
            ("Batch", batch_line.batch_no, batch_line.stock_lot),
            *(
                ("Serial No", serial_no, explicit_line.stock_lot)
                for serial_no in explicit_line.serial_numbers.splitlines()
            ),
            *(
                ("Serial No", serial_no, automatic_line.stock_lot)
                for serial_no in automatic_line.serial_numbers.splitlines()
            ),
        ]
        for doctype, tracking_name, stock_lot in tracked_masters:
            self.assertEqual(
                frappe.db.get_value(doctype, tracking_name, OWNERSHIP_FIELD),
                stock_lot,
            )

        for line in tracked_lines:
            lot = frappe.get_doc("CC Stock Lot", line.stock_lot)
            self.assertEqual(lot.tracking_type, line.tracking_type)
            self.assertEqual(lot.batch_no, line.batch_no)
            self.assertEqual(lot.serial_numbers, line.serial_numbers)
            self.assertEqual(get_ownership_balance(lot.name), line.stock_qty)

        batch_preview = preview_from_adapters(
            [CCStockLotCandidateAdapter()],
            query=CandidateQuery(
                item_code=batch_item,
                company=company.name,
                location=receipt.location,
                allowed_warehouses=frozenset({warehouses["COMMISSION"]}),
                batch_no=batch_line.batch_no,
            ),
            qty=Decimal("1"),
        )
        self.assertEqual(len(batch_preview), 1)
        self.assertEqual(batch_preview[0].lot_name, batch_line.stock_lot)
        self.assertEqual(batch_preview[0].batch_no, batch_line.batch_no)

        serial_preview = preview_from_adapters(
            [CCStockLotCandidateAdapter()],
            query=CandidateQuery(
                item_code=serial_item,
                company=company.name,
                location=receipt.location,
                allowed_warehouses=frozenset({warehouses["COMMISSION"]}),
                serial_no=explicit_serials[0],
            ),
            qty=Decimal("1"),
        )
        self.assertEqual(len(serial_preview), 1)
        self.assertEqual(serial_preview[0].lot_name, explicit_line.stock_lot)
        self.assertEqual(serial_preview[0].serial_no, explicit_serials[0])

        wrong_owner = explicit_line.stock_lot
        frappe.db.savepoint("cc_batch_cross_owner")
        with self.assertRaisesRegex(frappe.ValidationError, "belongs to"):
            issue = make_stock_entry(
                item_code=batch_item,
                company=company.name,
                from_warehouse=warehouses["COMMISSION"],
                qty=1,
                batch_no=batch_line.batch_no,
                use_serial_batch_fields=1,
                do_not_save=True,
            )
            issue.items[0].set(OWNERSHIP_FIELD, wrong_owner)
            issue.insert(ignore_permissions=True)
            issue.submit()
        frappe.db.rollback(save_point="cc_batch_cross_owner")

        frappe.db.savepoint("cc_batch_missing_owner")
        with self.assertRaisesRegex(frappe.ValidationError, "set the matching ownership"):
            issue = make_stock_entry(
                item_code=batch_item,
                company=company.name,
                from_warehouse=warehouses["COMMISSION"],
                qty=1,
                batch_no=batch_line.batch_no,
                use_serial_batch_fields=1,
                do_not_save=True,
            )
            issue.insert(ignore_permissions=True)
            issue.submit()
        frappe.db.rollback(save_point="cc_batch_missing_owner")

        frappe.db.savepoint("cc_serial_cross_owner")
        with self.assertRaisesRegex(frappe.ValidationError, "belongs to"):
            issue = make_stock_entry(
                item_code=serial_item,
                company=company.name,
                from_warehouse=warehouses["COMMISSION"],
                qty=1,
                serial_no=explicit_serials[0],
                use_serial_batch_fields=1,
                do_not_save=True,
            )
            issue.items[0].set(OWNERSHIP_FIELD, batch_line.stock_lot)
            issue.insert(ignore_permissions=True)
            issue.submit()
        frappe.db.rollback(save_point="cc_serial_cross_owner")

        frappe.db.savepoint("cc_tracking_transfer_preserves_owner")
        with self.assertRaisesRegex(frappe.ValidationError, "transfer must preserve"):
            transfer = make_stock_entry(
                item_code=batch_item,
                company=company.name,
                from_warehouse=warehouses["COMMISSION"],
                to_warehouse=warehouses["OWN"],
                qty=1,
                batch_no=batch_line.batch_no,
                use_serial_batch_fields=1,
                do_not_save=True,
            )
            transfer.items[0].set(OWNERSHIP_FIELD, batch_line.stock_lot)
            transfer.insert(ignore_permissions=True)
            transfer.submit()
        frappe.db.rollback(save_point="cc_tracking_transfer_preserves_owner")

        frappe.db.savepoint("cc_tracking_owner_immutable")
        batch = frappe.get_doc("Batch", batch_line.batch_no)
        batch.set(OWNERSHIP_FIELD, wrong_owner)
        with self.assertRaisesRegex(frappe.ValidationError, "ownership is immutable"):
            batch.save(ignore_permissions=True)
        frappe.db.rollback(save_point="cc_tracking_owner_immutable")

        frappe.db.savepoint("cc_tracking_owner_delete")
        with self.assertRaisesRegex(frappe.ValidationError, "cannot be deleted"):
            frappe.delete_doc("Batch", batch_line.batch_no, ignore_permissions=True)
        frappe.db.rollback(save_point="cc_tracking_owner_delete")

        receipt.cancel()
        for line in tracked_lines:
            self.assertEqual(get_ownership_balance(line.stock_lot), 0)
        for doctype, tracking_name, stock_lot in tracked_masters:
            self.assertEqual(
                frappe.db.get_value(doctype, tracking_name, OWNERSHIP_FIELD),
                stock_lot,
            )
