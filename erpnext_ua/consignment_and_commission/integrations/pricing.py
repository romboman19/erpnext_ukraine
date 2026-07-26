"""Effective immutable partner-price resolution for consignment sale slices."""

from __future__ import annotations

from typing import Any


class PriceResolutionError(ValueError):
    """Raised when exactly one effective partner price cannot be proven."""


def get_effective_price_version(stock_lot: str, at_datetime: Any) -> Any:
    import frappe
    from frappe.utils import get_datetime

    effective_at = get_datetime(at_datetime)
    rows = frappe.db.sql(
        """
        select name
        from `tabCC Price Version`
        where stock_lot = %s
          and docstatus = 1
          and valid_from <= %s
          and (valid_to is null or valid_to > %s)
        order by valid_from desc, name desc
        limit 2
        """,
        (stock_lot, effective_at, effective_at),
    )
    if not rows:
        raise PriceResolutionError(
            f"CC Stock Lot {stock_lot} has no approved partner price at {effective_at}"
        )
    if len(rows) != 1:
        raise PriceResolutionError(
            f"CC Stock Lot {stock_lot} has overlapping approved partner prices"
        )
    return frappe.get_doc("CC Price Version", rows[0][0])
