# ADR 0002 — Inventory Dimension scope and Sale Stage lifecycle

## Status

Accepted on 2026-07-27, after gates 0c and 0d.

## Context

GSF identifies a cost layer with an Inventory Dimension, mirroring how the
commission module identifies a lot (see the analogous
[CC ADR 0002](../../consignment/adr/0002-inventory-dimension-material-flow.md)).
Two questions were open going into Phase 0:

1. Does tagging a Stock Ledger Entry row with a layer dimension make ERPNext
   consume *that* layer at sale time, or does the dimension only travel along
   for the ride?
2. Can one Sale Stage warehouse per selling company serve concurrent
   checkouts, or does it need to be scoped tighter?

These are one decision, not two: the answer to the first question determines
whether the second is optional or mandatory.

## Evidence

Gate 0d ([evidence](../spikes/evidence/2026-07-27-gate-0d-layer-dimension.md))
confirmed the dimension reaches Stock Ledger Entry on every leg of a
reallocation, including the sale. On its own this evidence reads as if tagging
a row with a layer is sufficient to bind that row to that layer's cost.

Gate 0c ([evidence](../spikes/evidence/2026-07-27-gate-0c-sale-stage-cogs.md))
tested that reading directly and falsified it. Two runs prepared the same
layer — 2 units at a cost of 1000 each, moved from the source FOP's pool to the
seller's Sale Stage — and sold 2 units from it:

| Run | What else sat in the Sale Stage | Sale Invoice row tagged with | COGS charged |
|---|---|---|---:|
| A | nothing | the prepared layer | 2000 (correct) |
| B | 1 unit at 1500, a different layer | the prepared layer | **2500** |

Run B's invoice row carried the correct dimension value and ERPNext still
consumed the older, unrelated unit first. The dimension validates that a
negative-stock check happens per layer; it does not steer which layer a sale's
valuation draws from. That queue is ordered by `item_code + warehouse` only,
confirmed independently by gate 0f
([evidence](../spikes/evidence/2026-07-27-gate-0f-timestamp-ties.md)): at an
identical posting timestamp, ERPNext consumes whichever layer was **submitted
first into that warehouse**, with no regard for which document later claims it.

Put together, these three gates describe one concurrency hazard. Two cashiers
selling under the same FOP at the same location share one Sale Stage
warehouse. If checkout 1 reallocates a layer at cost 1000 and checkout 2
reallocates a different layer at cost 1100 into that same warehouse a few
seconds later, whichever Sales Invoice is submitted first drains the older
layer — regardless of which checkout that layer was reserved for. The
reallocation accounting (which source FOP's clearing account gets credited)
and the COGS both end up attached to the wrong checkout. A warehouse scoped to
"one till" does not close this: a single till can still have two in-flight
checkouts (a paused sale, a return interleaved with a new sale, a retry after a
network blip), and the failure mode is identical.

## Decision

**Sale Stage is scoped to one checkout, not to one company and not to one
till.** GSF creates a Sale Stage warehouse (or reservation-equivalent) per
`GSF Checkout`, receives exactly that checkout's reallocated layers into it,
and the warehouse holds nothing else for the lifetime of the checkout. It is
consumed by the checkout's own Sales Invoice and left empty or torn down
immediately after — mirroring the disposal gate 0d and 0e already exercise for
their own fixture warehouses.

A till-scoped or company-scoped Sale Stage was considered and rejected: it
reduces the collision window but does not close it, and a design that is
"usually correct" for a financial ledger is not an acceptable target.

Corollary, forced by the same evidence: **no application code may read a
layer's dimension value as its cost.** §16 already required reading cost from
the actual SLE rather than from GSF's own layer registry; gate 0c shows this is
not caution, it is the only correct behavior, because the dimension does not
participate in valuation selection at all. The dimension exists to (a) let a
layer be traced through the ledger and (b) let ERPNext's native negative-stock
check reject overselling a specific layer — nothing more.

## Consequences

- `GSF Checkout` (the route under `POS Order` per
  [ADR-013](0013-gsf-place-in-the-pos-saga.md)) owns creating and tearing down
  its own Sale Stage warehouse as part of its `PREPARING_STOCK` state. This is
  additional lifecycle the base spec did not scope this way.
- A busy location accumulates and discards many short-lived warehouses. This
  needs its own naming and cleanup contract — most likely one warehouse per
  checkout idempotency key, created lazily and archived (not deleted) once the
  checkout reaches a terminal state, so cancelled/failed checkouts remain
  auditable.
- Standard ERPNext warehouse reports and dropdowns will show these transient
  warehouses. A filtered view or a `disabled`/archived flag is required before
  this reaches cashier-facing UI; this is a follow-up UX item, not a blocker to
  the accounting model.
- The rollback proof from gate 0e already covers this shape of warehouse — it
  showed a Stock Entry/Sales Invoice chain unwinds cleanly and restores
  balances, and per-checkout warehouses do not change that result, only the
  warehouse name involved.
- No GSF service, report, or reconciliation may compute a cost from the layer
  registry as a substitute for the ledger. Any future code that does so is a
  defect against this ADR, not a valid optimization.

## Fallback

If per-checkout warehouse proliferation proves operationally unworkable (index
bloat, UI clutter beyond what an archived/disabled flag can hide), the fallback
is a small pool of pre-created, lock-checked-out Sale Stage warehouses per
till — checked out for the duration of one checkout and returned empty and
verified-empty before reuse. This still enforces "one checkout at a time per
warehouse"; it does not fall back to sharing a warehouse across concurrent
checkouts, which is the option this ADR rules out.
