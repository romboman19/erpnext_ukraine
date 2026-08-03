"""Functional Phase 3 acceptance run against the committed fixture.

The centrepiece is §37.1: the seller owns five of the ten units in the pool and
must still be served the other companies' older layers first. Everything else
here is the surrounding contract — idempotency, exhaustion, release — checked on
the same stock so the numbers stay comparable.

Build the fixture first, then::

    docker exec frappe-test-backend-1 bench --site postest.local execute \\
      erpnext_ua.group_stock_fifo.integration_tests.phase_3_checks.run
"""

from __future__ import annotations

from decimal import Decimal

import frappe

from ..services.allocations import consume_allocation, release_allocation, reserve
from ..services.domain import GSFError
from ..services.reservation import ReservationRequest
from .phase_3_fixture import GROUP, ITEM, LOCATION_CODE, assert_site, companies, pool_name


def run() -> dict:
    assert_site()
    firms = companies()
    seller = firms[2]
    pools = frozenset(pool_name(company) for company in firms)
    location = frappe.db.get_value(
        "GSF Physical Location", {"location_code": LOCATION_CODE}, "name"
    )
    unit_cost = {pool_name(firms[0]): 1000, pool_name(firms[1]): 1100, pool_name(firms[2]): 1200}
    out: dict = {}

    def request(**overrides):
        values = {
            "idempotency_key": "p3-check-1",
            "company_group": GROUP,
            "physical_location": location,
            "seller_company": seller,
            "item_code": ITEM,
            "qty": Decimal("6"),
            "allowed_warehouses": pools,
        }
        values.update(overrides)
        return ReservationRequest(**values)

    def fresh(fn):
        """§13.1 wants a clean transaction, so settle earlier reads first."""
        frappe.db.commit()
        return fn()

    def attempt(label, fn):
        try:
            out[label] = {"refused": False, "result": fresh(fn)}
        except GSFError as error:
            out[label] = {"refused": True, "code": error.code, "message": str(error)[:160]}
        except Exception as error:  # noqa: BLE001 - the point is to see what came out
            out[label] = {
                "refused": True,
                "error": type(error).__name__,
                "message": str(error)[:160],
            }

    def reserved_total():
        return frappe.db.sql("select sum(reserved_qty_cache) from `tabGSF Layer Balance`")[0][0]

    allocation = fresh(lambda: reserve(request()))
    out["reserved"] = {
        "name": allocation.name,
        "status": allocation.status,
        "allocated_qty": allocation.allocated_qty,
        "slices": [
            {
                "seq": row.sequence,
                "company": row.source_company,
                "warehouse": row.source_warehouse,
                "qty": row.qty,
                "fifo": str(row.original_fifo_datetime),
                "realloc": bool(row.requires_reallocation),
            }
            for row in allocation.slices
        ],
    }
    # §37.1 asks for 6500 across three owners. Layer cost is used here only to
    # show the selection was right — the real COGS comes from the ledger later.
    out["cogs_if_taken_at_layer_cost"] = sum(
        row.qty * unit_cost[row.source_warehouse] for row in allocation.slices
    )
    out["seller_owns_alone"] = frappe.db.sql(
        "select sum(actual_qty) from `tabStock Ledger Entry` "
        "where warehouse = %s and is_cancelled = 0",
        (pool_name(seller),),
    )[0][0]
    out["balances_after_reserve"] = frappe.get_all(
        "GSF Layer Balance",
        filters={"stock_layer": ("in", [row.stock_layer for row in allocation.slices])},
        fields=[
            "company",
            "warehouse",
            "actual_qty_cache",
            "reserved_qty_cache",
            "available_qty_cache",
        ],
        order_by="warehouse",
    )

    attempt("same_key_same_payload", lambda: reserve(request()).name)
    attempt("same_key_other_payload", lambda: reserve(request(qty=Decimal("2"))).name)
    attempt(
        "reserved_units_are_not_offered_twice",
        lambda: reserve(request(idempotency_key="p3-check-2", qty=Decimal("5"))).name,
    )
    attempt(
        "what_is_left_is_still_reservable",
        lambda: reserve(request(idempotency_key="p3-check-3", qty=Decimal("4"))).name,
    )
    attempt(
        "more_than_the_pool_holds",
        lambda: reserve(request(idempotency_key="p3-check-4", qty=Decimal("99"))).name,
    )
    attempt(
        "a_non_member_cannot_sell",
        lambda: reserve(
            request(idempotency_key="p3-check-5", seller_company="Not A Member Co")
        ).name,
    )
    attempt(
        "an_unknown_group_is_refused",
        lambda: reserve(
            request(idempotency_key="p3-check-6", company_group="No Such Group")
        ).name,
    )

    out["reserved_total_before_release"] = reserved_total()
    released = fresh(lambda: release_allocation(allocation.name, reason="phase 3 check"))
    out["after_release"] = {"status": released.status, "reserved_total": reserved_total()}
    # A repeated release must not decrement a second time: the reservation on a
    # position is one number shared by every allocation holding it.
    attempt("release_again", lambda: release_allocation(allocation.name, reason="again").status)
    out["reserved_total_after_second_release"] = reserved_total()

    remaining = frappe.get_all(
        "GSF Allocation", filters={"item_code": ITEM, "status": "RESERVED"}, pluck="name"
    )
    invoice = frappe.db.get_value("Sales Invoice", {}, "name")
    if remaining and invoice:
        attempt(
            "consume",
            lambda: consume_allocation(
                remaining[0], consumer_doctype="Sales Invoice", consumer_document=invoice
            ).status,
        )
        out["reserved_total_after_consume"] = reserved_total()
    else:
        out["consume"] = {"skipped": "no Sales Invoice on this site to point a consumer at"}

    frappe.db.commit()
    return out
