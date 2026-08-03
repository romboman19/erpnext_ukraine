"""One racer in the §37.7 double-booking check.

Two of these run in separate processes, on separate database connections, and
both try to take the whole pool at the same wall-clock instant. Exactly one may
win, and the reserved total must never exceed what the ledger holds. A single
process cannot prove this — one connection never contends with itself for a row
lock — which is why this is a pair of scripts rather than a `TestCase`.

Build the fixture first (see `phase_3_fixture`), then::

    START=$(python3 -c 'import time; print(time.time()+8)')
    for K in racer-a racer-b; do
      docker exec -e GSF_RACE_KEY=$K -e GSF_RACE_START=$START frappe-test-backend-1 \\
        bench --site postest.local execute \\
        erpnext_ua.group_stock_fifo.integration_tests.phase_3_race.run &
    done; wait
    docker exec frappe-test-backend-1 sh -c 'cat /tmp/gsf-race-*.json'
"""

from __future__ import annotations

import json
import os
import time
from decimal import Decimal

import frappe

from ..services.allocations import reserve
from ..services.reservation import ReservationRequest
from .phase_3_fixture import GROUP, ITEM, LOCATION_CODE, assert_site, companies, pool_name


def run() -> dict:
    assert_site()
    key = os.environ["GSF_RACE_KEY"]
    start_at = float(os.environ["GSF_RACE_START"])
    firms = companies()
    location = frappe.db.get_value(
        "GSF Physical Location", {"location_code": LOCATION_CODE}, "name"
    )

    request = ReservationRequest(
        idempotency_key=key,
        company_group=GROUP,
        physical_location=location,
        seller_company=firms[2],
        item_code=ITEM,
        qty=Decimal("10"),
        allowed_warehouses=frozenset(pool_name(company) for company in firms),
    )

    # Settle everything this process has read or written, so `reserve` starts in
    # the dedicated transaction §13.1 requires.
    frappe.db.commit()
    time.sleep(max(0.0, start_at - time.time()))

    began = time.time()
    try:
        allocation = reserve(request)
        frappe.db.commit()
        result = {
            "key": key,
            "won": True,
            "allocation": allocation.name,
            "allocated_qty": allocation.allocated_qty,
        }
    except Exception as error:  # noqa: BLE001 - losing the race is the expected path
        frappe.db.rollback()
        result = {
            "key": key,
            "won": False,
            "error": type(error).__name__,
            "code": getattr(error, "code", None),
            "message": str(error)[:150],
        }
    result["waited_seconds"] = round(time.time() - began, 3)

    with open(f"/tmp/gsf-race-{key}.json", "w") as handle:
        json.dump(result, handle, ensure_ascii=False)
    return result
