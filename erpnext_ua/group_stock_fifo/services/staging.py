"""Sale Stage lane acquisition and release (§9.8, ADR-006).

Gate 0c is why a lane may hold exactly one checkout at a time. It prepared the
right layer in a lane that also held one older unit, sold the prepared quantity,
and ERPNext charged the older unit's cost instead. Isolation is not tidiness
here — a lane with anything else in it produces a wrong COGS that no later check
recovers from.

Hence the zero-balance check is a precondition of the lock rather than a
formality, and a lane that fails it becomes `DIRTY` and stays that way: §44
forbids cleaning one automatically, because whatever is sitting there is stock
somebody's accounts still believe in.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from .domain import (
    LANE_AVAILABLE,
    LANE_DIRTY,
    LANE_LOCKED,
    GSFError,
    LaneFacts,
    check_lane_available,
)


def acquire_lane(*, company: str, physical_location: str, checkout: str) -> str:
    """§13.2 level 1 — the first lock any preparation takes.

    Taken before the scope and layer locks so that two checkouts contending for
    the same lane queue here, where the wait is cheap, rather than after they
    have already locked stock rows.
    """
    if not checkout:
        raise GSFError("A lane is locked by a checkout, not by nothing", "STAGE_LANE_BUSY")

    candidates = frappe.get_all(
        "GSF Staging Lane",
        filters={
            "company": company,
            "physical_location": physical_location,
            "enabled": 1,
            "status": ("in", (LANE_AVAILABLE, LANE_LOCKED)),
        },
        order_by="last_used_at asc, name asc",
        pluck="name",
    )
    if not candidates:
        raise GSFError(
            f"{company} has no usable staging lane at {physical_location}", "STAGE_LANE_BUSY"
        )

    for name in candidates:
        if _try_lock(name, checkout=checkout):
            return name
    raise GSFError(
        f"Every staging lane of {company} at {physical_location} is busy", "STAGE_LANE_BUSY"
    )


def _try_lock(name: str, *, checkout: str) -> bool:
    """Lock one lane, or report that it was not free after all."""
    rows = frappe.db.sql(
        """
        select name, lane_code, status, enabled, current_checkout, warehouse
        from `tabGSF Staging Lane` where name = %s for update
        """,
        (name,),
        as_dict=True,
    )
    if not rows:
        return False
    lane = rows[0]
    if lane.status == LANE_LOCKED and lane.current_checkout == checkout:
        return True

    held = non_zero_items(lane.warehouse)
    try:
        check_lane_available(
            LaneFacts(
                lane_code=lane.lane_code,
                status=lane.status,
                enabled=bool(lane.enabled),
                current_checkout=lane.current_checkout,
                non_zero_items=held,
            ),
            checkout=checkout,
        )
    except GSFError as error:
        if error.code == "STAGE_LANE_DIRTY" and held:
            _mark_dirty(name, reason=f"Holds {', '.join(held)} before lock")
            raise
        return False

    frappe.db.sql(
        """
        update `tabGSF Staging Lane`
        set status = %(locked)s, current_checkout = %(checkout)s, lock_token = %(token)s,
            last_zero_check = %(now)s, last_used_at = %(now)s, modified = %(now)s
        where name = %(name)s and status = %(available)s
        """,
        {
            "locked": LANE_LOCKED,
            "available": LANE_AVAILABLE,
            "checkout": checkout,
            "token": frappe.generate_hash(length=20),
            "now": now_datetime(),
            "name": name,
        },
    )
    return int(frappe.db.sql("select row_count()")[0][0]) == 1


def release_lane(name: str, *, checkout: str) -> str:
    """Give the lane back, or mark it dirty if it did not come back empty.

    A lane that still holds stock after a checkout is the §37.11 case: something
    was prepared and never sold, and the next checkout must not inherit it.
    """
    rows = frappe.db.sql(
        "select name, current_checkout, warehouse from `tabGSF Staging Lane` "
        "where name = %s for update",
        (name,),
        as_dict=True,
    )
    if not rows:
        raise GSFError(f"Staging lane {name} does not exist", "STAGE_LANE_BUSY")
    lane = rows[0]
    if lane.current_checkout and lane.current_checkout != checkout:
        raise GSFError(
            f"Staging lane {name} is held by checkout {lane.current_checkout}", "STAGE_LANE_BUSY"
        )

    held = non_zero_items(lane.warehouse)
    if held:
        _mark_dirty(name, reason=f"Held {', '.join(held)} after checkout {checkout}")
        raise GSFError(
            f"Staging lane {name} still holds {', '.join(held)} and needs an operator",
            "STAGE_LANE_DIRTY",
        )

    frappe.db.sql(
        """
        update `tabGSF Staging Lane`
        set status = %(available)s, current_checkout = null, lock_token = null,
            last_zero_check = %(now)s, modified = %(now)s
        where name = %(name)s
        """,
        {"available": LANE_AVAILABLE, "now": now_datetime(), "name": name},
    )
    return LANE_AVAILABLE


def non_zero_items(warehouse: str) -> tuple[str, ...]:
    """§9.8: every Item in the lane must be at zero, read from Bin not from cache."""
    rows = frappe.db.sql(
        "select item_code from `tabBin` where warehouse = %s and actual_qty != 0 order by item_code",
        (warehouse,),
        pluck=True,
    )
    return tuple(rows)


def _mark_dirty(name: str, *, reason: str) -> None:
    """A dirty lane is never cleaned automatically (§44) — only recorded."""
    frappe.db.sql(
        """
        update `tabGSF Staging Lane`
        set status = %(dirty)s, dirty_reason = %(reason)s, modified = %(now)s
        where name = %(name)s
        """,
        {"dirty": LANE_DIRTY, "reason": reason, "now": now_datetime(), "name": name},
    )
