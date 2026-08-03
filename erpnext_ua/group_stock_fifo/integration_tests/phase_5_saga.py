"""The checkout saga, end to end and then deliberately broken (§23).

Two runs on one fixture. The first walks a basket all the way to a completed
sale. The second takes an identical basket to `STOCK_PREPARED` and aborts it,
which is the case §23.2 exists for: stock is sitting in a lane with no sale
behind it, so the abort owes a compensation rather than a release.

Build the fixture first, then::

    docker exec frappe-test-backend-1 bench --site postest.local execute \\
      erpnext_ua.group_stock_fifo.integration_tests.phase_5_saga.run
"""

from __future__ import annotations

from decimal import Decimal

import frappe

from ..services.checkout import (
    CheckoutLine,
    CheckoutRequest,
    abort,
    open_checkout,
)
from ..services.checkout import run as walk
from ..services.domain import GSFError
from .phase_3_fixture import (
    CUSTOMER,
    GROUP,
    ITEM,
    LOCATION_CODE,
    assert_site,
    companies,
    drain_reposts,
    pool_name,
    stage_name,
)


def run() -> dict:
    assert_site()
    firms = companies()
    seller = firms[2]
    stage = stage_name(seller)
    pools = [pool_name(company) for company in firms]
    location = frappe.db.get_value(
        "GSF Physical Location", {"location_code": LOCATION_CODE}, "name"
    )
    out: dict = {}

    def settle():
        frappe.db.commit()
        out.setdefault("reposts_drained", []).append(drain_reposts())

    def qty(warehouse):
        return float(
            frappe.db.sql(
                "select coalesce(sum(actual_qty), 0) from `tabStock Ledger Entry` "
                "where warehouse = %s and is_cancelled = 0",
                (warehouse,),
            )[0][0]
        )

    def snapshot():
        return {"pools": {name: qty(name) for name in pools}, "stage": qty(stage)}

    def request(key, lines):
        return CheckoutRequest(
            idempotency_key=key,
            company_group=GROUP,
            physical_location=location,
            seller_company=seller,
            customer=CUSTOMER,
            lines=tuple(
                CheckoutLine(item_code=ITEM, qty=Decimal(str(q)), rate=Decimal(str(r)))
                for q, r in lines
            ),
        )

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

    out["before"] = snapshot()

    # ---- an aborted checkout, stopped after its stock was staged ----------
    frappe.db.commit()
    aborted = open_checkout(request("saga-abort", [(4, 2000)]))
    settle()
    # Stop after preparation: stock staged, no sale behind it, which is exactly
    # the state §23.2 says owes a compensation rather than a release.
    aborted = walk(aborted.name, stop_at="STOCK_PREPARED")
    settle()
    out["aborted_before_abort"] = {"status": aborted.status, **snapshot()}

    aborted = abort(aborted.name, reason="saga check")
    settle()
    out["aborted_after"] = {
        "status": aborted.status,
        "stock_state": aborted.stock_state,
        **snapshot(),
        "pools_restored": snapshot()["pools"] == out["before"]["pools"],
        "lane": frappe.db.get_value(
            "GSF Staging Lane", {"warehouse": stage}, ["status", "current_checkout"], as_dict=True
        ),
    }
    attempt("aborting_twice_is_a_no_op", lambda: abort(aborted.name, reason="again").status)

    # ---- a checkout walked all the way through ---------------------------
    frappe.db.commit()
    sold = walk(open_checkout(request("saga-sale", [(6, 2000)])).name)
    settle()
    out["sold"] = {
        "status": sold.status,
        "stock_state": sold.stock_state,
        "erp_sale_state": sold.erp_sale_state,
        "fiscal_state": sold.fiscal_state,
        "sales_invoice": sold.sales_invoice,
        "lines": [{"qty": row.qty, "allocation": row.allocation} for row in sold.lines],
        **snapshot(),
    }
    from ..services.sale import actual_sale_cogs

    out["sold_cogs"] = float(actual_sale_cogs(sold.sales_invoice))
    out["invoice_rows"] = frappe.db.count("Sales Invoice Item", {"parent": sold.sales_invoice})
    out["lane_after_sale"] = frappe.db.get_value(
        "GSF Staging Lane", {"warehouse": stage}, ["status", "current_checkout"], as_dict=True
    )

    attempt("walking_a_completed_checkout_again", lambda: walk(sold.name).status)
    # A finished sale is not a stopped one: aborting it is a category error and
    # should say so, rather than quietly return "already done".
    attempt(
        "a_completed_checkout_cannot_be_aborted",
        lambda: abort(sold.name, reason="too late").status,
    )
    attempt(
        "the_same_key_with_a_different_basket",
        lambda: open_checkout(request("saga-sale", [(1, 2000)])).name,
    )

    frappe.db.commit()
    return out
