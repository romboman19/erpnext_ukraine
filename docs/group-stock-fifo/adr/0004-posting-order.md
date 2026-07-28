# ADR 0004 — Posting order

## Status

Accepted on 2026-07-27, after gates 0f and 0j. Numbered per
[spec](../spec-v1.0.md) §40.

Its accounting content moved to
[ADR-003](0003-exact-value-intercompany-reallocation.md) and
[ADR-005](0005-balance-sheet-clearing-accounting.md), which are the slots §40
assigns to it.

## Context

§14.5 requires stock preparation to be unambiguously earlier than the sale SLE,
in the order `source issue → destination receipt / own transfer → Sales
Invoice`, and warns against relying on incidental `creation` order. It also
contemplates posting-time offsets as a possible safeguard, with conditions: not
crossing a day boundary, reproducible, business date preserved, captured in an
audit snapshot.

Two orderings are actually at stake, and the spec addresses them in different
places:

1. the order of the three documents relative to each other (§14.5);
2. the order in which several reallocated layers enter the same Sale Stage —
   which decides which of them a subsequent sale consumes first (§12.3, §7.3).

## Evidence

Gate 0f ([evidence](../spikes/evidence/2026-07-27-gate-0f-timestamp-ties.md))
answered the second, harder question. Two layers at an identical
`posting_datetime`, one cheap and one dear, in both submission orders:

| Run | Submitted first | Submitted second | Consumed |
|---|---:|---:|---:|
| A | 1000 | 2000 | **1000** |
| B | 2000 | 1000 | **2000** |
| C — repeat of A | 1000 | 2000 | **1000** |

The layer submitted first is consumed first, stably across three runs. Run B
rules out coincidence with the rate; run C rules out a one-off.

Gate 0j ([evidence](../spikes/evidence/2026-07-27-gate-0j-end-to-end.md)) then
executed a three-company plan in allocator order and the sale charged exactly
the planned 6500.

## Decision

**Submission order is the tie-breaker, and it is therefore a contract.**
Reallocation documents are submitted in the sequence the allocator produced.
This is not an implementation preference; it is the mechanism by which §12.3's
FIFO key survives into the physical warehouse.

**Posting-time offsets are not adopted.** §14.5 allowed them if needed. They are
not needed: submission order already produces a deterministic and controllable
outcome, and synthetic sub-second timestamps would distort reporting to solve a
problem that no longer exists. Should a future ERPNext version change the
tie-break behaviour, §14.5's conditions remain the sanctioned fallback and this
ADR must be revisited.

**Document sequence stays as §14.5 specifies:** source issue, then destination
receipt or own transfer, then the Sales Invoice. Gate 0j executed it in that
order and gate 0e proved the whole chain unwinds together.

**Handler order on `Sales Invoice.on_submit`:** the GSF consume handler runs
next to the commission one and before `ua_fiscal.sales_invoice.on_submit`.
Everything that can fail happens before a fiscal receipt exists — the revision's
§29.2 reading, and a precondition of
[ADR-012](0012-pos-prro-saga.md).

**A slice already owned by the seller moves by Material Transfer**, per §14.2,
not through the clearing account. Gate 0j pushed all three slices through
clearing to keep one code path in the spike; it nets to zero and is harmless in
a test, but it is the wrong document for an intra-company move and must not
reach Phase 1.

## Consequences

- The allocator's output order is load-bearing data, not a display detail. It is
  persisted on `GSF Allocation Slice.sequence` (§9.13) and the preparation
  service must not reorder it — for example by grouping documents per company
  for convenience (§14.4).
- §14.4 permits grouping several layers into one Material Issue per source
  company. Grouping is allowed only where it preserves relative order of the
  rows within the document; the exact grouping scheme still needs its own
  decision after an accounting prototype, as §14.4 requires.
- The intra-company Material Transfer path is a second document shape to build
  and test, not a variant of the first.
- Gate 0f used one warehouse in one company. Ordering across warehouses of
  different FOPs is decided by the allocator, not by ERPNext, so it is out of
  this ADR's scope.
