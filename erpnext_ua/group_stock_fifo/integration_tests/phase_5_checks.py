"""Phase 5 acceptance run: the §37.1 chain all the way to a sold invoice.

Phase 3 chose the stock, Phase 4 moved it. This sells it and asks the one
question §16.4 cares about: did ERPNext charge exactly the value the stage was
prepared with. Then it compensates a second checkout to prove a committed
reallocation can be undone without erasing anything.

Build the fixture first, then::

    docker exec frappe-test-backend-1 bench --site postest.local execute \\
      erpnext_ua.group_stock_fifo.integration_tests.phase_5_checks.run
"""

from __future__ import annotations

from decimal import Decimal

import frappe

from ..services.allocations import reserve
from ..services.compensation import compensate
from ..services.domain import GSFError
from ..services.reallocation import prepare
from ..services.reservation import ReservationRequest
from ..services.sale import actual_sale_cogs, sell
from ..setup.layer_dimension import DISPLAY_GROUP_FIELD, LAYER_FIELD
from .phase_3_fixture import (
    CUSTOMER,
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

    def request(key, qty, checkout):
        return ReservationRequest(
            idempotency_key=key,
            company_group=GROUP,
            physical_location=location,
            seller_company=seller,
            item_code=ITEM,
            qty=Decimal(str(qty)),
            allowed_warehouses=pools,
            checkout=checkout,
        )

    def warehouse_qty(warehouse):
        return frappe.db.sql(
            "select coalesce(sum(actual_qty), 0) from `tabStock Ledger Entry` "
            "where warehouse = %s and is_cancelled = 0",
            (warehouse,),
        )[0][0]

    # ---- the sale -------------------------------------------------------
    frappe.db.commit()
    allocation = reserve(request("p5-sale", 6, "P5-CHECKOUT-1"))
    reallocation = prepare(allocation.name, checkout="P5-CHECKOUT-1")
    invoice = sell(
        allocation.name, customer=CUSTOMER, rate=2000, checkout="P5-CHECKOUT-1"
    )
    frappe.db.commit()

    out["invoice"] = {
        "name": invoice.name,
        "grand_total": invoice.grand_total,
        "rows": [
            {
                "qty": row.qty,
                "rate": row.rate,
                "warehouse": row.warehouse,
                "layer": row.get(LAYER_FIELD),
                "display_group": row.get(DISPLAY_GROUP_FIELD),
            }
            for row in invoice.items
        ],
    }
    # §16.4 — the one number this whole phase exists to get right.
    out["cogs"] = {
        "actual_sale_cogs": float(actual_sale_cogs(invoice.name)),
        "expected_from_37_1": 6500.0,
    }
    out["stage_after_sale"] = float(warehouse_qty(stage))
    out["lane_after_sale"] = frappe.db.get_value(
        "GSF Staging Lane", {"warehouse": stage}, ["status", "current_checkout"], as_dict=True
    )
    out["allocation_after_sale"] = frappe.db.get_value(
        "GSF Allocation", allocation.name, "status"
    )
    out["reallocation_after_sale"] = frappe.db.get_value(
        "GSF Stock Reallocation", reallocation.name, "status"
    )
    out["sale_movements"] = frappe.get_all(
        "GSF Layer Movement",
        filters={"movement_type": "SALE_CONSUMPTION"},
        fields=["qty", "stock_value"],
        order_by="qty",
    )

    # ---- the compensation ----------------------------------------------
    pools_before = {name: float(warehouse_qty(name)) for name in sorted(pools)}
    second = reserve(request("p5-compensate", 3, "P5-CHECKOUT-2"))
    second_reallocation = prepare(second.name, checkout="P5-CHECKOUT-2")
    frappe.db.commit()
    out["before_compensation"] = {
        "pools": {name: float(warehouse_qty(name)) for name in sorted(pools)},
        "stage": float(warehouse_qty(stage)),
    }

    compensated = compensate(second_reallocation.name, reason="phase 5 check")
    frappe.db.commit()
    out["after_compensation"] = {
        "status": compensated.status,
        "pools": {name: float(warehouse_qty(name)) for name in sorted(pools)},
        "pools_match_before": {name: float(warehouse_qty(name)) for name in sorted(pools)}
        == pools_before,
        "stage": float(warehouse_qty(stage)),
        "allocation": frappe.db.get_value("GSF Allocation", second.name, "status"),
        "lane": frappe.db.get_value(
            "GSF Staging Lane", {"warehouse": stage}, ["status", "current_checkout"], as_dict=True
        ),
        "reversals": frappe.db.count("GSF Layer Movement", {"is_reversal": 1}),
    }
    out["clearing_after_compensation"] = frappe.db.sql(
        """
        select acc.account_name as account, sum(gl.debit - gl.credit) as balance
        from `tabGL Entry` gl join `tabAccount` acc on acc.name = gl.account
        where gl.is_cancelled = 0 and acc.account_name like 'GSF Internal Stock Due%%'
        group by acc.account_name order by acc.account_name
        """,
        as_dict=True,
    )

    attempt(
        "compensating_twice_is_a_no_op",
        lambda: compensate(second_reallocation.name, reason="again").status,
    )
    attempt(
        "a_consumed_reallocation_cannot_be_compensated",
        lambda: compensate(reallocation.name, reason="too late").status,
    )

    frappe.db.commit()
    return out
