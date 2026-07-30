"""Phase 4 acceptance run: §37.1's numbers, this time in the ledger.

Phase 3 proved the *selection* was right — 2 + 3 + 1 across three owners for a
seller who owned five units of its own. This run moves that stock and checks the
three things §15 and §16 actually promise:

* the seller's stage receives exactly what the sources issued, to the tolerance;
* neither company's profit and loss is touched by the move;
* the layer identity survives both legs, so the sale can still be traced back.

Build the fixture first, then::

    docker exec frappe-test-backend-1 bench --site postest.local execute \\
      erpnext_ua.group_stock_fifo.integration_tests.phase_4_checks.run
"""

from __future__ import annotations

from decimal import Decimal

import frappe

from ..services.allocations import reserve
from ..services.domain import GSFError
from ..services.reallocation import prepare
from ..services.reservation import ReservationRequest
from .phase_3_fixture import (
    GROUP,
    ITEM,
    LOCATION_CODE,
    assert_site,
    companies,
    pool_name,
    stage_name,
)


def run() -> dict:
    assert_site()
    firms = companies()
    seller = firms[2]
    pools = frozenset(pool_name(company) for company in firms)
    stage = stage_name(seller)
    location = frappe.db.get_value(
        "GSF Physical Location", {"location_code": LOCATION_CODE}, "name"
    )
    out: dict = {}

    def attempt(label, fn):
        frappe.db.commit()
        try:
            out[label] = {"refused": False, "result": fn()}
        except GSFError as error:
            out[label] = {"refused": True, "code": error.code, "message": str(error)[:200]}
        except Exception as error:  # noqa: BLE001
            out[label] = {
                "refused": True,
                "error": type(error).__name__,
                "message": str(error)[:200],
            }

    frappe.db.commit()
    allocation = reserve(
        ReservationRequest(
            idempotency_key="p4-check-1",
            company_group=GROUP,
            physical_location=location,
            seller_company=seller,
            item_code=ITEM,
            qty=Decimal("6"),
            allowed_warehouses=pools,
            checkout="P4-CHECKOUT-1",
        )
    )
    out["allocation"] = {"name": allocation.name, "status": allocation.status}

    reallocation = prepare(allocation.name, checkout="P4-CHECKOUT-1")
    frappe.db.commit()
    reallocation.reload()

    out["reallocation"] = {
        "name": reallocation.name,
        "status": reallocation.status,
        "clearing_status": reallocation.clearing_status,
        "total_qty": reallocation.total_qty,
        "total_source_value": reallocation.total_source_value,
        "total_destination_value": reallocation.total_destination_value,
        "value_difference": reallocation.value_difference,
        "legs": [
            {
                "source_company": leg.source_company,
                "same_company": bool(leg.is_same_company_transfer),
                "qty": leg.qty,
                "source_value": leg.source_stock_value,
                "destination_value": leg.destination_stock_value,
                "difference": leg.difference,
                "issue": leg.source_issue,
                "receipt": leg.destination_receipt,
                "counterparty": leg.counterparty_company,
            }
            for leg in reallocation.legs
        ],
    }

    vouchers = [leg.source_issue for leg in reallocation.legs] + [
        leg.destination_receipt for leg in reallocation.legs if leg.destination_receipt
    ]
    out["stage_ledger"] = frappe.db.sql(
        """
        select warehouse, sum(actual_qty) as qty, sum(stock_value_difference) as value,
               count(distinct gsf_stock_layer) as layers
        from `tabStock Ledger Entry`
        where warehouse = %s and is_cancelled = 0
        group by warehouse
        """,
        (stage,),
        as_dict=True,
    )
    # §15.1: a management reallocation has no profit-and-loss effect at all.
    out["pnl_touched"] = frappe.db.sql(
        """
        select coalesce(sum(gl.debit - gl.credit), 0)
        from `tabGL Entry` gl
        join `tabAccount` acc on acc.name = gl.account
        where gl.voucher_no in %(vouchers)s and gl.is_cancelled = 0
          and acc.root_type in ('Income', 'Expense')
        """,
        {"vouchers": tuple(vouchers)},
    )[0][0]
    out["clearing_gl"] = frappe.db.sql(
        """
        select acc.name as account, acc.root_type, sum(gl.debit - gl.credit) as balance
        from `tabGL Entry` gl
        join `tabAccount` acc on acc.name = gl.account
        where gl.voucher_no in %(vouchers)s and gl.is_cancelled = 0
          and acc.account_name like 'GSF Internal Stock Due%%'
        group by acc.name, acc.root_type
        order by acc.name
        """,
        {"vouchers": tuple(vouchers)},
        as_dict=True,
    )
    out["layer_identity_survived"] = frappe.db.sql(
        """
        select gsf_stock_layer as layer, sum(actual_qty) as qty
        from `tabStock Ledger Entry`
        where warehouse = %s and is_cancelled = 0
        group by gsf_stock_layer order by gsf_stock_layer
        """,
        (stage,),
        as_dict=True,
    )
    out["source_pools_after"] = frappe.db.sql(
        """
        select warehouse, sum(actual_qty) as qty from `tabStock Ledger Entry`
        where warehouse in %(pools)s and is_cancelled = 0
        group by warehouse order by warehouse
        """,
        {"pools": tuple(sorted(pools))},
        as_dict=True,
    )
    out["lane"] = frappe.db.get_value(
        "GSF Staging Lane", {"warehouse": stage}, ["status", "current_checkout"], as_dict=True
    )
    out["allocation_after"] = frappe.db.get_value(
        "GSF Allocation", allocation.name, ["status", "positions_released"], as_dict=True
    )
    out["reserved_left"] = frappe.db.sql(
        "select coalesce(sum(reserved_qty_cache), 0) from `tabGSF Layer Balance`"
    )[0][0]
    out["movements"] = frappe.get_all(
        "GSF Layer Movement",
        filters={"movement_type": ("in", ("INTERCOMPANY_ISSUE", "INTERCOMPANY_RECEIPT", "OWN_POOL_TO_STAGE"))},
        fields=["movement_type", "qty", "stock_value"],
        order_by="movement_type, qty",
    )

    attempt("preparing_twice", lambda: prepare(allocation.name, checkout="P4-CHECKOUT-1").name)

    # A genuinely second checkout, on its own allocation. Reusing the first one
    # would fail on the status transition long before reaching the lane, which
    # would prove nothing about lane isolation.
    second = reserve(
        ReservationRequest(
            idempotency_key="p4-check-2",
            company_group=GROUP,
            physical_location=location,
            seller_company=seller,
            item_code=ITEM,
            qty=Decimal("1"),
            allowed_warehouses=pools,
            checkout="P4-CHECKOUT-2",
        )
    )
    attempt(
        "a_second_checkout_cannot_take_the_locked_lane",
        lambda: prepare(second.name, checkout="P4-CHECKOUT-2").name,
    )

    frappe.db.commit()
    return out
