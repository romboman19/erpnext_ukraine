# ADR 0007 — Valuation queue preflight

## Status

Accepted on 2026-07-28, after gate 0k
([evidence](../spikes/evidence/2026-07-28-gate-0k-valuation-preflight.md)).

Filed as `Proposed` on 2026-07-27 with no spike behind it; the spike now exists
and answered §42.1 question 7.

## Context

§17.1 states the problem: GSF's layer dimension keeps an exact **quantity**
audit, but ERPNext values non-tracked stock on a warehouse-level queue. If the
layer ledger and the actual valuation queue drift apart, a Material Issue
releases a different value than the global layer order implies.

This is risk #1 in §35, and §17.2 makes a preflight check mandatory before every
source issue.

## Evidence

Gate 0k built the preflight and measured it against the real ledger.

**The queue needs no reconstruction.** ERPNext persists its FIFO queue as JSON on
every Stock Ledger Entry (`stock_queue`) and ships the class that consumes it
(`erpnext.stock.valuation.FIFOValuation`). Reading the latest row for an
`item + warehouse` pair and replaying it through that class predicts the next
issue exactly — in memory, with no write.

| Run | Planned | Predicted | Actual | Outcome |
|---|---:|---:|---:|---|
| A — spanning two layers | 4200.00 | 4200.00 | **4200.00** | queue untouched by the prediction |
| B — 1 unit with no layer | 4200.00 | 4200.00 | — | `UNCLASSIFIED_GSF_STOCK` |
| C — plan disagrees | 4000.00 | 4200.00 | — | `VALUATION_QUEUE_DIVERGENCE` |

Run B is the instructive one: its **value** check passes, because the offending
unit sits third in the queue and does not affect an issue of four. A
value-only preflight would have let it through and failed on a later checkout.

The failure shape the preflight has to catch was established earlier.

Gate 0c ([evidence](../spikes/evidence/2026-07-27-gate-0c-sale-stage-cogs.md))
demonstrated divergence at the destination: a stage holding foreign stock
charged 2500 where 2000 was prepared. The same mechanism applies at the
**source**, which is where §17 aims — if the source OWN Pool contains stock the
layer ledger does not know about, or knows about in a different order, the issue
releases the wrong number and every downstream equality check inherits it.

Gate 0f ([evidence](../spikes/evidence/2026-07-27-gate-0f-timestamp-ties.md))
established that ERPNext's own consumption order is deterministic and follows
submission order into the warehouse. That is what makes a preflight feasible at
all: a non-deterministic platform could not be predicted, only reconciled after
the fact.

Gate 0g ([evidence](../spikes/evidence/2026-07-27-gate-0g-shared-allocator.md))
originally carried the question "reproduce the local valuation queue" and was
**re-scoped away from it** to answer the allocator question instead. The
re-scoping note argued the queue question is a consequence of §16 rather than a
gate of its own. Now that the spec is in the repository, that argument is
visibly too weak: §17 is a mandatory subsystem, not a corollary, and §42.1
question 7 asks for it directly.

## Decision

**Read the queue from `Stock Ledger Entry.stock_queue` and replay it through
ERPNext's own `FIFOValuation`.** Option 1 of the three candidates; options 2
(reconstruct from ledger rows) and 3 (savepoint dry-run) are not needed and are
not implemented. Reusing the platform's class rather than reimplementing FIFO is
the point: a divergence between prediction and behaviour would require ERPNext to
diverge from itself.

**A preflight runs before every source Material Issue, for each
source Company / Item / Warehouse triple, and blocks on mismatch with
`VALUATION_QUEUE_DIVERGENCE`.** No warning-and-continue: §44 forbids passing a
valuation mismatch as a warning.

The preflight must establish, per §17.2:

1. the quantity ERPNext will consume next matches the GSF-selected layers in the
   same order — or at minimum yields the exact expected total value for the full
   selected quantity;
2. there is no unclassified stock in the warehouse (stock without
   `gsf_stock_layer`), raising `UNCLASSIFIED_GSF_STOCK`;
3. there is no pending backdated repost, raising `PENDING_REPOST`;
4. there is no negative stock, raising `NEGATIVE_STOCK_RISK`.

**Total value is the binding criterion, not layer-by-layer order.** §16.3
already settles equality on total value; requiring identical ordering would
reject cases that are financially correct. Order equality is checked and
reported, but only the total gates the transaction.

**Divergence is repaired, never absorbed.** On mismatch the checkout stops and a
`GSF Integrity Issue` is raised for manual review (§17.2). Automatic repair is
forbidden without dry-run and approval (§27.2, §35 risk 30).

## Resolved: how to obtain the queue

§42.1 question 7 asked how to obtain or reproduce the local valuation queue
reliably. Answer: read `stock_queue` off the latest SLE. The three candidates
this ADR listed while unproven resolve as follows.

1. **Read ERPNext's own queue — adopted.** It is exposed, stable, and consumed
   by a public class.
2. Reconstruct from active ledger rows — unnecessary, and would be a second
   implementation of FIFO to keep in step with the first.
3. Savepoint dry-run — unnecessary. Gate 0e proved the rollback is clean, so it
   would have worked, but it doubles writes on the hot path for no gain.

The §17.3 minimisation rules still apply and reduce how often the preflight can
fail: accept into GSF OWN Pool only with a layer; forbid unmanaged Stock Entry;
move reallocated layers straight into the staging lane rather than the seller's
OWN Pool; make returns a new layer by default; control backdated documents; run
an integrity check after Landed Cost or Repost.

## Still open

Two of the four §17.2 checks were not exercised: `PENDING_REPOST` (the test stack
has no scheduler) and `NEGATIVE_STOCK_RISK`. Both read existing tables and do not
depend on the queue mechanism, but neither has been demonstrated.

Serial and Batch items keep their queue in `Serial and Batch Bundle` rather than
in `stock_queue`. The preflight as proved does not cover them, and §21.2/§21.3
route those items through exact selection anyway — but the gap should be closed
before tracked items enter a GSF pool.

## Consequences

- Risk #1 in §35 now has a working mitigation rather than a planned one.
- The preflight is two indexed reads and an in-memory replay, so it is cheap
  enough to sit on the checkout hot path. §34.2 still tracks the latency.
- §17.3's minimisation rules remain worth implementing early, in Phase 2 with
  the layer registry, because they reduce how often the preflight has to refuse.
- Gate 0k also established that a Material Issue row cannot span two layers —
  the dimension's negative-stock check rejects it. §14.4 and §18.2 are therefore
  enforced by the platform, not merely recommended, and the preparation service
  must build one row per slice.
