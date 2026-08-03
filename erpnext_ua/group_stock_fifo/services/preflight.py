"""§17 preflight: refuse to issue stock ERPNext would value differently.

Gate 0c is the whole reason this module exists. It sold two units that carried
the correct layer dimension and ERPNext still charged 2500 instead of 2000,
because it consumed an older unrelated unit sitting in the same warehouse. The
dimension records which layer a row *claims*; the warehouse queue decides which
stock is actually consumed. When those two disagree, the sale is wrong and
nothing downstream can detect it.

This module is the decision alone, over facts someone else gathered — it
imports no Frappe, so §28.3 lets it be tested without a site. `preflight_probe`
reads those facts off the ledger.

The preflight asks two separate questions:

* **Would ERPNext consume exactly the layers GSF selected?** Answered from
  GSF's own ledger positions, ordered by the §12.3 FIFO key. If the warehouse
  holds older stock the plan did not select, the answer is no.
* **What value will that consumption carry?** Answered by replaying the
  warehouse's persisted `stock_queue` through ERPNext's own `FIFOValuation` —
  gate 0k's finding that no reconstruction is needed. In memory, no write, no
  savepoint.

The second is recorded for the audit trail; §16.2 still reads the real value
from the ledger after the issue, because a prediction is never the source of a
number that lands in the accounts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .domain import GSFError


@dataclass(frozen=True, slots=True)
class QueueFacts:
    """Everything the §17.2 decision needs, gathered before deciding anything."""

    item_code: str
    warehouse: str
    requested_qty: Decimal
    selected: dict[str, Decimal]
    expected: dict[str, Decimal]
    unclassified_qty: Decimal = Decimal("0")
    pending_repost: bool = False
    negative_stock: bool = False
    predicted_value: Decimal = Decimal("0")
    predicted_bins: tuple[tuple[Decimal, Decimal], ...] = ()


@dataclass(slots=True)
class PreflightReport:
    warehouse: str
    item_code: str
    requested_qty: Decimal
    predicted_value: Decimal
    error_code: str | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error_code is None


def evaluate(facts: QueueFacts) -> PreflightReport:
    """The §17.2 decision, as a pure function over gathered facts.

    Ordered by how much the failure tells you: unclassified stock and a pending
    repost mean the warehouse is not in a state where *any* prediction holds, so
    they are reported before the comparison that assumes it is.
    """
    report = PreflightReport(
        warehouse=facts.warehouse,
        item_code=facts.item_code,
        requested_qty=facts.requested_qty,
        predicted_value=facts.predicted_value,
    )

    if facts.unclassified_qty:
        report.error_code = "UNCLASSIFIED_GSF_STOCK"
        report.reasons.append(
            f"{facts.warehouse} holds {facts.unclassified_qty} of {facts.item_code} "
            "with no layer, which the layer ledger cannot account for"
        )
        return report

    if facts.pending_repost:
        report.error_code = "PENDING_REPOST"
        report.reasons.append(
            f"A repost is still queued for {facts.item_code} in {facts.warehouse}; "
            "the valuation queue is not settled"
        )
        return report

    if facts.negative_stock:
        report.error_code = "NEGATIVE_STOCK_RISK"
        report.reasons.append(f"{facts.warehouse} already carries negative stock")
        return report

    if facts.selected != facts.expected:
        report.error_code = "VALUATION_QUEUE_DIVERGENCE"
        report.reasons.extend(_describe_divergence(facts))
        return report

    return report


def _describe_divergence(facts: QueueFacts) -> list[str]:
    """Name the layers, not just the fact. A bare refusal is unactionable."""
    reasons = []
    for layer in sorted(set(facts.expected) | set(facts.selected)):
        planned = facts.selected.get(layer, Decimal("0"))
        actual = facts.expected.get(layer, Decimal("0"))
        if planned == actual:
            continue
        if not planned:
            reasons.append(f"ERPNext would consume {actual} from {layer}, which GSF did not select")
        elif not actual:
            reasons.append(f"GSF selected {planned} from {layer}, which is not next in the queue")
        else:
            reasons.append(f"{layer}: GSF selected {planned}, ERPNext would consume {actual}")
    return reasons


def assert_ok(reports: list[PreflightReport]) -> None:
    """Fail closed on the first refusal, carrying its §33 code outwards."""
    for report in reports:
        if not report.ok:
            raise GSFError(
                f"Preflight refused {report.item_code} in {report.warehouse}: "
                + "; ".join(report.reasons),
                report.error_code,
            )
