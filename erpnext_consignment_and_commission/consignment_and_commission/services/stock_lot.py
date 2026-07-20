"""ERPNext Stock Ledger query boundary for ownership-lot quantities."""

from __future__ import annotations

from decimal import Decimal

from ..setup.ownership_dimension import OWNERSHIP_FIELD


def get_ownership_balance(lot_name: str) -> Decimal:
    """Return active quantity for one CC Stock Lot from ERPNext SLE.

    ERPNext's warehouse-level ``qty_after_transaction`` is intentionally not
    used because it is not a dimension balance when ownership lots coexist.
    """
    import frappe

    lot = frappe.db.get_value(
        "CC Stock Lot",
        lot_name,
        ["item_code", "warehouse"],
        as_dict=True,
    )
    if not lot:
        raise ValueError(f"CC Stock Lot {lot_name!r} does not exist")
    if not frappe.db.has_column("Stock Ledger Entry", OWNERSHIP_FIELD):
        raise RuntimeError("CC Stock Lot Inventory Dimension is not synchronized; run bench migrate")

    balance = frappe.db.sql(
        f"""
        select coalesce(sum(actual_qty), 0)
        from `tabStock Ledger Entry`
        where item_code = %s
          and warehouse = %s
          and `{OWNERSHIP_FIELD}` = %s
          and is_cancelled = 0
        """,
        (lot.item_code, lot.warehouse, lot_name),
    )[0][0]
    return Decimal(str(balance or 0))
