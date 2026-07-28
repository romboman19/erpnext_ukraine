# ADR 0007 — Valuation queue preflight

## Status

Proposed on 2026-07-27. **Not yet backed by a spike** — this is the one §40 ADR
whose evidence Phase 0 did not gather.

Blocks Phase 4 (§41). No reallocation may post on a production site before this
is accepted and its control implemented.

## Context

§17.1 states the problem: GSF's layer dimension keeps an exact **quantity**
audit, but ERPNext values non-tracked stock on a warehouse-level queue. If the
layer ledger and the actual valuation queue drift apart, a Material Issue
releases a different value than the global layer order implies.

This is risk #1 in §35, and §17.2 makes a preflight check mandatory before every
source issue.

## Evidence available so far

Phase 0 did not build the preflight. What it did establish is the shape of the
failure the preflight has to catch.

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

## Open — must be answered by a spike before this ADR is accepted

§42.1 question 7 remains unanswered: **how to obtain or reproduce the local
valuation queue reliably.** Candidate approaches, in order of preference:

1. read ERPNext's own FIFO queue representation for the bin directly, if v16
   exposes it in a stable form;
2. reconstruct it from active `Stock Ledger Entry` rows for the
   `item + warehouse` pair and compare against the GSF layer balances;
3. dry-run the issue in a savepoint and read the resulting SLE, then roll back —
   gate 0e proved this rollback is clean, which makes the approach viable if the
   first two are not.

Option 3 is the fallback of last resort: it doubles the write load on the hot
path. Options 1 and 2 must be evaluated first, and the spike must state which
one it proves.

The §17.3 minimisation rules apply regardless of which approach wins: accept
into GSF OWN Pool only with a layer; forbid unmanaged Stock Entry; move
reallocated layers straight into the Sale Stage rather than the seller's OWN
Pool; make returns a new layer by default; control backdated documents; run an
integrity check after Landed Cost or Repost.

## Consequences

- Until the spike lands, Phase 0's verdict of `GO WITH CONSTRAINTS` should be
  read as covering the stock and accounting mechanics only. The control that
  §35 names as the mitigation for its highest risk does not exist yet.
- The preflight sits on the checkout hot path, so its cost is a latency budget
  item (§34.2 tracks allocation and checkout latency).
- §17.3's minimisation rules are cheaper than the preflight itself and reduce
  how often it can fail. They should be implemented first, in Phase 2 with the
  layer registry, not deferred to Phase 4 with the preflight.
