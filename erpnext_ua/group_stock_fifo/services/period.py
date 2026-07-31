"""Backdated guards and period close (§25).

§25.1 names the risk plainly: a receipt posted into the past, a landed cost, a
repost — any of them can change the global FIFO order, the local valuation
queue, a historical COGS, and the value of an intercompany transfer that has
already been made and booked in two companies. The reallocation cannot be
un-made retroactively; only the numbers around it can move, and then they no
longer agree with what was posted.

So the MVP does not attempt to recompute history. It refuses to let history
change (§25.2), and closes periods only when nothing is outstanding (§25.4).
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import get_datetime, getdate, now_datetime

from .domain import OWN_POOL_ROLE, SALE_STAGE_ROLE, GSFError
from .integrity import check as integrity_check
from .reservation import LIVE_ALLOCATION_STATUSES


def guard_backdated_document(doc, method=None) -> None:
    """§25.2: nothing posts into a closed period, or before it (`doc_events`).

    Inert unless the document touches a GSF warehouse, so an ordinary ERPNext
    or commission-domain posting never notices this runs.
    """
    if not frappe.db.get_single_value("GSF Settings", "enabled"):
        return
    if not frappe.db.get_single_value("GSF Settings", "block_backdated_mutations"):
        return
    closed_through = _closed_through()
    if not closed_through:
        return

    posting_date = getdate(doc.get("posting_date") or now_datetime())
    if posting_date > getdate(closed_through):
        return
    if not _touches_gsf(doc):
        return

    frappe.throw(
        f"{doc.doctype} is dated {posting_date}, on or before the closed period "
        f"{closed_through}. Reopening the period is a deliberate act, not a side effect "
        "of a posting.",
        title="CLOSED_PERIOD",
    )


def _closed_through():
    """The close date, or None.

    An unset Frappe Date field reads back as `0001-01-01` rather than nothing,
    and treating that as a real close date would make every posting look
    backdated against the year one.
    """
    value = frappe.db.get_single_value("GSF Settings", "closed_through_date")
    if not value or getdate(value).year <= 1:
        return None
    return value


def _touches_gsf(doc) -> bool:
    warehouses = {
        value
        for child in doc.get("items") or []
        for value in (
            child.get("warehouse"),
            child.get("s_warehouse"),
            child.get("t_warehouse"),
        )
        if value
    }
    if not warehouses:
        return False
    return bool(
        frappe.db.exists(
            "GSF Warehouse Binding",
            {
                "warehouse": ("in", list(warehouses)),
                "enabled": 1,
                "manager_app": "GSF",
                "warehouse_role": ("in", (OWN_POOL_ROLE, SALE_STAGE_ROLE)),
            },
        )
    )


def close_period(*, closed_through: str, company_group: str | None = None) -> dict[str, Any]:
    """§25.4: close only when nothing is outstanding, and record why it was safe.

    The blockers are not bureaucratic. An open allocation holds stock that the
    closed period's numbers already treat as settled; a dirty lane holds stock
    nobody has accounted for; a critical integrity finding means the derived
    numbers and their sources disagree, and closing over that makes the
    disagreement permanent.
    """
    blockers: list[str] = []

    open_allocations = frappe.get_all(
        "GSF Allocation",
        filters={
            "status": ("in", LIVE_ALLOCATION_STATUSES),
            **({"company_group": company_group} if company_group else {}),
        },
        pluck="name",
        limit=50,
    )
    if open_allocations:
        blockers.append(f"{len(open_allocations)} allocation(s) still hold stock")

    open_reallocations = frappe.get_all(
        "GSF Stock Reallocation",
        filters={
            "status": ("in", ("DRAFT", "VALIDATING", "POSTING_SOURCE", "POSTING_DESTINATION",
                              "PREPARED", "COMPENSATING", "MANUAL_REVIEW")),
            **({"company_group": company_group} if company_group else {}),
        },
        pluck="name",
        limit=50,
    )
    if open_reallocations:
        blockers.append(f"{len(open_reallocations)} reallocation(s) are unfinished")

    open_checkouts = frappe.get_all(
        "GSF Checkout",
        filters={
            "status": ("not in", ("COMPLETED", "CANCELLED", "COMPENSATED", "RETURNED")),
            **({"company_group": company_group} if company_group else {}),
        },
        pluck="name",
        limit=50,
    )
    if open_checkouts:
        blockers.append(f"{len(open_checkouts)} checkout(s) are still in flight")

    report = integrity_check(company_group)
    for finding in report.critical:
        blockers.append(f"{finding.code} on {finding.subject}: {finding.detail}")

    if blockers:
        raise GSFError(
            "Period cannot close:\n" + "\n".join(f"- {line}" for line in blockers),
            "CLOSED_PERIOD",
        )

    previous = _closed_through()
    if previous and getdate(closed_through) < getdate(previous):
        raise GSFError(
            f"The period is already closed through {previous}; closing backwards would "
            "reopen it silently",
            "CLOSED_PERIOD",
        )

    settings = frappe.get_single("GSF Settings")
    settings.closed_through_date = closed_through
    settings.save(ignore_permissions=True)

    return {
        "closed_through": closed_through,
        "previous": str(previous) if previous else None,
        "closed_at": str(now_datetime()),
        "integrity": report.as_dict(),
    }


def flag_repost_as_integrity_issue(doc, method=None) -> None:
    """§25.2: a completed repost may have moved values GSF already acted on.

    Recorded rather than acted on. What a repost changed cannot be inferred
    from the repost itself, and guessing would be worse than telling someone.
    """
    if not frappe.db.get_single_value("GSF Settings", "enabled"):
        return
    if doc.get("status") != "Completed":
        return
    if not frappe.db.exists(
        "GSF Warehouse Binding",
        {"warehouse": doc.get("warehouse"), "enabled": 1, "manager_app": "GSF"},
    ):
        return
    frappe.log_error(
        title="GSF: valuation repost completed on a managed warehouse",
        message=(
            f"Repost {doc.name} finished for {doc.get('item_code')} in "
            f"{doc.get('warehouse')} at {get_datetime(doc.get('modified'))}. "
            "Run the Financial Integrity report before the next period close (§25.2)."
        ),
    )
