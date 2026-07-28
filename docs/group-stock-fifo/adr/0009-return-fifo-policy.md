# ADR 0009 — Return FIFO policy

## Status

Accepted on 2026-07-28. Numbered per [spec](../spec-v1.0.md) §40. Derived from
§19 and §17.3; no spike was required, and the reason is recorded below.

## Context

A customer return puts stock back into the group. Two questions follow: which
company books it, and where the returned quantity lands in the global FIFO
order.

The second is not cosmetic. Restoring the layer's original receipt date inserts
stock into the *past* of the global queue, and the local ERPNext valuation queue
will not agree — it appends the return at the end, because that is when the
movement happened.

## Evidence

No gate was run for returns, and none is needed to adopt the default: gate 0k
already established why restoring an old FIFO date is unsafe.

The preflight reads `Stock Ledger Entry.stock_queue`, which is ERPNext's own
FIFO queue in arrival order. A returned unit enters that queue **at the end**,
at the rate the return posts. If GSF simultaneously claimed the layer sits at
the *front* of the global order because of its original receipt date, the two
orderings disagree permanently, and gate 0k's preflight would report
`VALUATION_QUEUE_DIVERGENCE` on the next issue from that warehouse — correctly,
because the divergence would be real.

In other words, §19.2's stated reason is confirmed by the mechanism gate 0k
exposed, without needing a separate experiment.

## Decision

**The returning company is the company that made the sale**, per §19.1. The
company that originally received the stock is irrelevant to the return. This
follows from the sale being an external transaction of the seller: its revenue,
its fiscal receipt, its reversal.

**A non-tracked return creates a new `GSF Stock Layer`**, per §19.2:

```text
origin_company = seller Company
original_received_datetime = return posting datetime
return_origin_layer = the layer originally sold
```

The link back to the sold layer preserves the audit trail without reintroducing
the old date into the ordering.

**Tracked items go to quarantine by default.** Serial and Batch stock returns to
`GSF_RETURN_QUARANTINE` (§7.2) and does not participate in automatic FIFO until
inspected, per §19.3. Exact identity restore stays possible but requires its own
decision — §19.3 says so, and this ADR does not grant it.

**Before the sale is submitted, a cancellation is not a return**, per §19.4:
release the reservation, compensate or roll back the stock preparation, return
the staging lane to zero, and create no return layer. Gate 0e proved the
rollback half of this is clean.

**After the sale is submitted or fiscalized**, only the controlled
return/correction workflow applies. Audit documents are never deleted; reversal
links are created (§34.3, §44).

## Consequences

- A returned unit is genuinely newer than everything already in the pool, and
  will be sold last. That is the correct answer for FIFO cost, and it may look
  wrong to an operator who expects the physical item to keep its history. The
  `GSF Stranded Stock` report (§31.5) is where that shows up, and the return
  link makes it explainable.
- `return_origin_layer` gives the chain sold-layer → return-layer, so §31.3's
  audit can follow a physical unit across a return even though its FIFO identity
  changed.
- Quarantine is a warehouse role that has to exist before tracked items are sold
  at all, otherwise the first tracked return has nowhere to go.
- The staging lane must be back at zero before a cancellation completes
  ([ADR-006](0006-stage-lane-isolation.md)); a return that leaves stock in a lane
  turns it `DIRTY` and blocks the next checkout, which is the intended
  fail-closed behaviour.
- Not covered: a return of a unit whose original layer has since been fully
  consumed and closed. The link still resolves because layers are immutable
  (§9.9), but the reporting consequence has not been examined.
