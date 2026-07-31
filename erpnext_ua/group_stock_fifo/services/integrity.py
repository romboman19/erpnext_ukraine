"""`GSF Financial Integrity` (§31): the checks that decide whether the books hold.

Everything GSF writes is derived from something else — the layer cache from the
ledger, the clearing balances from the reallocation legs, the stage from the
lane's own contents. Derived data drifts, and §9.10 is explicit that a cache may
lag but may never *hide* a divergence. This is what makes that promise
enforceable: it recomputes each derived number from its source and reports where
the two disagree.

It is also the gate on closing a period (§25.4), which is why every finding
carries a severity rather than a bare message. A stale cache can wait for the
next repair pass; clearing balances that do not net to zero cannot, because the
group's own books no longer agree with themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import frappe

from ..setup.layer_dimension import LAYER_FIELD
from .domain import OWN_POOL_ROLE, SALE_STAGE_ROLE
from .reservation import LIVE_ALLOCATION_STATUSES

CRITICAL = "CRITICAL"
WARNING = "WARNING"

#: Anything above this in base currency is a real disagreement rather than
#: rounding. Read from settings so one number governs both this and §16.2.
DEFAULT_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Finding:
    severity: str
    code: str
    subject: str
    detail: str


@dataclass(slots=True)
class IntegrityReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def critical(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == CRITICAL]

    @property
    def ok(self) -> bool:
        return not self.critical

    def add(self, severity: str, code: str, subject: str, detail: str) -> None:
        self.findings.append(Finding(severity, code, subject, detail))

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "critical",
            "critical_count": len(self.critical),
            "findings": [
                {
                    "severity": finding.severity,
                    "code": finding.code,
                    "subject": finding.subject,
                    "detail": finding.detail,
                }
                for finding in self.findings
            ],
        }


def check(company_group: str | None = None) -> IntegrityReport:
    """Run every integrity check, optionally narrowed to one group."""
    report = IntegrityReport()
    tolerance = Decimal(
        str(frappe.db.get_single_value("GSF Settings", "currency_tolerance") or DEFAULT_TOLERANCE)
    )
    _check_clearing(report, company_group=company_group, tolerance=tolerance)
    _check_layer_cache(report, company_group=company_group, tolerance=tolerance)
    _check_unclassified_stock(report, company_group=company_group)
    _check_idle_stages(report, company_group=company_group)
    _check_stale_allocations(report, company_group=company_group)
    return report


def _check_clearing(report: IntegrityReport, *, company_group: str | None, tolerance: Decimal) -> None:
    """Due-from must equal due-to, per group, counterparty and reallocation.

    This is the check ADR-005 promised when it moved the reconciliation key off
    the accounting dimension ERPNext would not create and onto the leg itself.
    """
    rows = frappe.db.sql(
        """
        select realloc.company_group, realloc.name as reallocation,
               leg.source_company, leg.destination_company,
               sum(leg.source_stock_value) as issued,
               sum(leg.destination_stock_value) as received
        from `tabGSF Reallocation Leg` leg
        join `tabGSF Stock Reallocation` realloc on realloc.name = leg.parent
        where leg.is_same_company_transfer = 0
          and realloc.status in ('PREPARED', 'CONSUMED')
          and (%(group)s is null or realloc.company_group = %(group)s)
        group by realloc.company_group, realloc.name,
                 leg.source_company, leg.destination_company
        """,
        {"group": company_group},
        as_dict=True,
    )
    for row in rows:
        gap = abs(Decimal(str(row.issued or 0)) - Decimal(str(row.received or 0)))
        if gap > tolerance:
            report.add(
                CRITICAL,
                "CLEARING_IMBALANCE",
                row.reallocation,
                f"{row.source_company} issued {row.issued} but {row.destination_company} "
                f"received {row.received}",
            )


def _check_layer_cache(report: IntegrityReport, *, company_group: str | None, tolerance: Decimal) -> None:
    """§9.10: the cache may lag the ledger, but it may not disagree with it."""
    rows = frappe.db.sql(
        f"""
        select balance.name, balance.stock_layer, balance.warehouse,
               balance.actual_qty_cache as cached,
               coalesce(ledger.qty, 0) as actual
        from `tabGSF Layer Balance` balance
        join `tabGSF Stock Layer` layer on layer.name = balance.stock_layer
        left join (
            select `{LAYER_FIELD}` as stock_layer, warehouse, sum(actual_qty) as qty
            from `tabStock Ledger Entry`
            where is_cancelled = 0 and `{LAYER_FIELD}` is not null and `{LAYER_FIELD}` != ''
            group by `{LAYER_FIELD}`, warehouse
        ) ledger on ledger.stock_layer = balance.stock_layer
                and ledger.warehouse = balance.warehouse
        where (%(group)s is null or layer.company_group = %(group)s)
        """,
        {"group": company_group},
        as_dict=True,
    )
    for row in rows:
        gap = abs(Decimal(str(row.cached or 0)) - Decimal(str(row.actual or 0)))
        if gap > tolerance:
            report.add(
                CRITICAL,
                "LAYER_BALANCE_DIVERGENCE",
                row.name,
                f"{row.stock_layer} in {row.warehouse}: cache says {row.cached}, "
                f"the ledger says {row.actual}",
            )


def _check_unclassified_stock(report: IntegrityReport, *, company_group: str | None) -> None:
    """§17.3: stock in a GSF pool with no layer is stock nothing can account for."""
    rows = frappe.db.sql(
        f"""
        select sle.warehouse, sle.item_code, sum(sle.actual_qty) as qty
        from `tabStock Ledger Entry` sle
        join `tabGSF Warehouse Binding` binding on binding.warehouse = sle.warehouse
        where sle.is_cancelled = 0
          and (sle.`{LAYER_FIELD}` is null or sle.`{LAYER_FIELD}` = '')
          and binding.enabled = 1 and binding.manager_app = 'GSF'
          and binding.warehouse_role in (%(pool)s, %(stage)s)
          and (%(group)s is null or binding.company_group = %(group)s)
        group by sle.warehouse, sle.item_code
        having sum(sle.actual_qty) != 0
        """,
        {"group": company_group, "pool": OWN_POOL_ROLE, "stage": SALE_STAGE_ROLE},
        as_dict=True,
    )
    for row in rows:
        report.add(
            CRITICAL,
            "UNCLASSIFIED_GSF_STOCK",
            row.warehouse,
            f"{row.qty} of {row.item_code} carries no layer",
        )


def _check_idle_stages(report: IntegrityReport, *, company_group: str | None) -> None:
    """A lane holding stock with no checkout behind it is §37.11's stranded case."""
    lanes = frappe.get_all(
        "GSF Staging Lane",
        filters={"company_group": company_group} if company_group else {},
        fields=["name", "warehouse", "status", "current_checkout", "dirty_reason"],
    )
    for lane in lanes:
        if lane.status == "DIRTY":
            report.add(
                CRITICAL, "STAGE_LANE_DIRTY", lane.name, lane.dirty_reason or "no reason recorded"
            )
            continue
        held = frappe.db.sql(
            "select item_code, actual_qty from `tabBin` where warehouse = %s and actual_qty != 0",
            (lane.warehouse,),
            as_dict=True,
        )
        if held and not lane.current_checkout:
            report.add(
                CRITICAL,
                "STAGE_LANE_DIRTY",
                lane.name,
                "holds "
                + ", ".join(f"{row.actual_qty} of {row.item_code}" for row in held)
                + " with no checkout holding it",
            )


def _check_stale_allocations(report: IntegrityReport, *, company_group: str | None) -> None:
    """Expired reservations still holding stock. A warning: the sweeper fixes it."""
    from frappe.utils import now_datetime

    stale = frappe.get_all(
        "GSF Allocation",
        filters={
            "status": ("in", LIVE_ALLOCATION_STATUSES),
            "expires_at": ("<=", now_datetime()),
            **({"company_group": company_group} if company_group else {}),
        },
        fields=["name", "status", "expires_at"],
        limit=200,
    )
    for allocation in stale:
        report.add(
            WARNING,
            "ALLOCATION_EXPIRED",
            allocation.name,
            f"{allocation.status} but expired at {allocation.expires_at}; "
            "expire_due_allocations has not run",
        )
