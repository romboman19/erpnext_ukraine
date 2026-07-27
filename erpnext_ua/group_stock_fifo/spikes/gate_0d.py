"""Gate 0d — does the layer dimension reach the stock ledger on both legs?

GSF identifies a cost layer with an Inventory Dimension. The reallocation has
two legs in two different companies, and the sale is a third document. If the
dimension does not survive all three, the layer cannot be followed through the
ledger and §16 has nothing to read the cost from.

The gate also answers half of 0h: it puts a second dimension next to the
commission one and checks both survive together.

The carrier DocType is `GSF Spike Layer`, created with `custom = 1`. It
deliberately does not squat the production name, so a future file-based
`GSF Stock Layer` cannot collide with a database-only leftover.

    bench --site postest.local execute \\
      erpnext_ua.group_stock_fifo.spikes.gate_0d.run \\
      --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0D"}'
"""

from __future__ import annotations

import json
from typing import Any

from .fixtures import FOPS, ITEM_CODE, assert_scope
from .stock_setup import (
    SPIKE_MARKER,
    cancel_spike_entries,
    ensure_customer,
    income_account,
    issue_to_clearing,
    purge_orphan_ledger_rows,
    receive_layer,
)

CONFIRMATION = "RUN_GATE_0D"
CLEANUP_CONFIRMATION = "CLEAN_GATE_0D"

LAYER_DOCTYPE = "GSF Spike Layer"
# On Stock Entry Detail the dimension is two fields, not one: the plain name
# carries the outgoing leg and the `to_` prefix carries the incoming one.
DIMENSION_FIELD = "gsf_spike_layer"
INCOMING_DIMENSION_FIELD = "to_gsf_spike_layer"
CC_DIMENSION = "CC Stock Lot"

SOURCE = FOPS[0]
SELLER = FOPS[2]


def run(confirm_site: str, confirm_write: str) -> dict[str, Any]:
    import frappe

    assert_scope(frappe, confirm_site=confirm_site, confirm_write=confirm_write, expected=CONFIRMATION)
    report = _Gate0D(frappe).run()
    frappe.db.commit()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def cleanup(confirm_site: str, confirm_write: str) -> dict[str, Any]:
    """Drop the dimension and its carrier, leaving the schema as it was."""
    import frappe

    assert_scope(frappe, confirm_site=confirm_site, confirm_write=confirm_write, expected=CLEANUP_CONFIRMATION)
    removed = cancel_spike_entries(frappe)
    purge_orphan_ledger_rows(frappe, ITEM_CODE)
    for doctype, name in (("Inventory Dimension", LAYER_DOCTYPE), ("DocType", LAYER_DOCTYPE)):
        if frappe.db.exists(doctype, name):
            frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
            removed.append(f"{doctype} {name}")
    frappe.db.commit()
    report = {
        "site": frappe.local.site,
        "removed": removed,
        "sle_column_present": frappe.db.has_column("Stock Ledger Entry", DIMENSION_FIELD),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


class _Gate0D:
    def __init__(self, frappe: Any) -> None:
        self.frappe = frappe
        self.date = frappe.utils.nowdate()

    def run(self) -> dict[str, Any]:
        cancelled = cancel_spike_entries(self.frappe)
        purge_orphan_ledger_rows(self.frappe, ITEM_CODE)
        schema = self._ensure_dimension()
        ensure_customer(self.frappe)
        self.frappe.db.commit()

        source_layer = self._new_layer(SOURCE.company)
        target_layer = self._new_layer(SELLER.company, source=source_layer)
        legs = self._move_and_sell(source_layer, target_layer)

        checks = {
            "sle_column_created": schema["sle_column_present"],
            "both_dimensions_coexist": schema["dimensions"] == [CC_DIMENSION, LAYER_DOCTYPE],
            "cc_column_untouched": schema["cc_column_present"],
            "issue_carries_source_layer": legs["issue"]["dimension"] == source_layer,
            "receipt_carries_target_layer": legs["receipt"]["dimension"] == target_layer,
            "sale_carries_target_layer": legs["sale"]["dimension"] == target_layer,
        }
        return {
            "site": self.frappe.local.site,
            "cancelled_before_run": cancelled,
            "schema": schema,
            "layers": {"source": source_layer, "target": target_layer},
            "legs": legs,
            "checks": checks,
            "result": "PASS" if all(checks.values()) else "FAIL",
        }

    def _ensure_dimension(self) -> dict[str, Any]:
        if not self.frappe.db.exists("DocType", LAYER_DOCTYPE):
            self.frappe.get_doc(
                {
                    "doctype": "DocType",
                    "name": LAYER_DOCTYPE,
                    "module": "Stock",
                    "custom": 1,
                    "autoname": "GSF-LAYER-.#####",
                    "fields": [
                        {"fieldname": "item_code", "fieldtype": "Link", "options": "Item", "label": "Item"},
                        {
                            "fieldname": "owner_company",
                            "fieldtype": "Link",
                            "options": "Company",
                            "label": "Owner Company",
                        },
                        {
                            "fieldname": "source_layer",
                            "fieldtype": "Link",
                            "options": LAYER_DOCTYPE,
                            "label": "Source Layer",
                        },
                    ],
                    "permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1}],
                }
            ).insert(ignore_permissions=True)

        if not self.frappe.db.exists("Inventory Dimension", LAYER_DOCTYPE):
            self.frappe.get_doc(
                {
                    "doctype": "Inventory Dimension",
                    "dimension_name": LAYER_DOCTYPE,
                    "reference_document": LAYER_DOCTYPE,
                    "apply_to_all_doctypes": 1,
                    "validate_negative_stock": 1,
                }
            ).insert(ignore_permissions=True)
            self.frappe.clear_cache()

        return {
            "sle_column_present": bool(self.frappe.db.has_column("Stock Ledger Entry", DIMENSION_FIELD)),
            "cc_column_present": bool(self.frappe.db.has_column("Stock Ledger Entry", "cc_stock_lot")),
            "dimensions": sorted(self.frappe.db.sql_list("select name from `tabInventory Dimension`")),
        }

    def _new_layer(self, company: str, source: str | None = None) -> str:
        doc = self.frappe.get_doc(
            {
                "doctype": LAYER_DOCTYPE,
                "item_code": ITEM_CODE,
                "owner_company": company,
                "source_layer": source,
            }
        ).insert(ignore_permissions=True)
        return doc.name

    def _move_and_sell(self, source_layer: str, target_layer: str) -> dict[str, Any]:
        seed = receive_layer(
            self.frappe,
            company=SOURCE.company,
            warehouse=SOURCE.pool_warehouse,
            item_code=ITEM_CODE,
            qty=2,
            rate=1000,
            posting_date=self.date,
            posting_time="08:00:00",
            label="D-seed",
            extra={INCOMING_DIMENSION_FIELD: source_layer},
        )
        issue = issue_to_clearing(
            self.frappe,
            company=SOURCE.company,
            warehouse=SOURCE.pool_warehouse,
            item_code=ITEM_CODE,
            qty=2,
            posting_date=self.date,
            posting_time="09:00:00",
            label="D-issue",
            extra={DIMENSION_FIELD: source_layer},
        )
        receipt = receive_layer(
            self.frappe,
            company=SELLER.company,
            warehouse=SELLER.stage_warehouse,
            item_code=ITEM_CODE,
            qty=2,
            rate=1000,
            posting_date=self.date,
            posting_time="09:00:00",
            label="D-receipt",
            extra={INCOMING_DIMENSION_FIELD: target_layer},
        )
        sale = self._sell(target_layer)
        return {
            "seed": self._leg(seed),
            "issue": self._leg(issue),
            "receipt": self._leg(receipt),
            "sale": self._leg(sale),
        }

    def _sell(self, target_layer: str) -> str:
        doc = self.frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "company": SELLER.company,
                "customer": ensure_customer(self.frappe),
                "set_posting_time": 1,
                "posting_date": self.date,
                "posting_time": "10:00:00",
                "update_stock": 1,
                "remarks": f"{SPIKE_MARKER} D-sale",
                "items": [
                    {
                        "item_code": ITEM_CODE,
                        "qty": 2,
                        "rate": 1800,
                        "warehouse": SELLER.stage_warehouse,
                        "income_account": income_account(self.frappe, SELLER.company),
                        DIMENSION_FIELD: target_layer,
                    }
                ],
            }
        )
        doc.insert(ignore_permissions=True)
        doc.submit()
        return doc.name

    def _leg(self, voucher_no: str) -> dict[str, Any]:
        row = self.frappe.db.sql(
            f"""
            select warehouse, actual_qty, stock_value_difference, `{DIMENSION_FIELD}` as dimension
            from `tabStock Ledger Entry`
            where voucher_no = %s and is_cancelled = 0
            order by creation limit 1
            """,
            voucher_no,
            as_dict=True,
        )
        first = row[0] if row else {}
        return {
            "voucher": voucher_no,
            "warehouse": first.get("warehouse"),
            "qty": float(first.get("actual_qty") or 0),
            "value": float(first.get("stock_value_difference") or 0),
            "dimension": first.get("dimension"),
        }
