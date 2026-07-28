# ADR 0002 — Inventory Dimension coexistence

## Status

Accepted on 2026-07-27, after gates 0c and 0d. Numbered per
[spec](../spec-v1.0.md) §40.

The Sale Stage half of the original text moved to
[ADR-006](0006-stage-lane-isolation.md), which is the slot §40 assigns to it,
and its decision was replaced there by the spec's staging-lane pool.

## Context

GSF identifies a cost layer with an Inventory Dimension, mirroring how the
commission module identifies a lot (see the analogous
[CC ADR 0002](../../consignment/adr/0002-inventory-dimension-material-flow.md)).
Two questions were open going into Phase 0:

1. Does tagging a Stock Ledger Entry row with a layer dimension make ERPNext
   consume *that* layer at sale time, or does the dimension only travel along
   for the ride?
2. `apply_to_all_doctypes = 1` stamped the GSF field onto commission DocTypes
   that never asked for it. Is a narrower scope available, and if so, which?

§10.2 already asserts the answer to the first: "Dimension не гарантує окрему
valuation queue для кожного шару." What was missing was proof, and the price of
ignoring it. That proof also decides how tightly the Sale Stage must be scoped,
which is why gate 0c appears both here and in
[ADR-006](0006-stage-lane-isolation.md).

## Evidence

Gate 0d ([evidence](../spikes/evidence/2026-07-27-gate-0d-layer-dimension.md))
confirmed the dimension reaches Stock Ledger Entry on every leg of a
reallocation, including the sale. On its own this evidence reads as if tagging
a row with a layer is sufficient to bind that row to that layer's cost.

Gate 0c ([evidence](../spikes/evidence/2026-07-27-gate-0c-sale-stage-cogs.md))
tested that reading directly and falsified it. Two runs prepared the same
layer — 2 units at a cost of 1000 each, moved from the source FOP's pool to the
seller's staging lane — and sold 2 units from it:

| Run | What else sat in the lane | Sale Invoice row tagged with | COGS charged |
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

Separately, gate 0d ([evidence](../spikes/evidence/2026-07-27-gate-0d-layer-dimension.md))
found that `apply_to_all_doctypes = 1` stamped a GSF custom field onto eight
commission DocTypes (`CC Stock Lot`, `CC Allocation`, `CC Receipt Item` among
them) that GSF never writes to. A follow-up probe on `postest.local` checked
whether ERPNext's Inventory Dimension supports a curated multi-DocType scope
instead of "all or one":

```python
frappe.get_doc({
    "doctype": "Inventory Dimension",
    "dimension_name": "GSF Probe Explicit",
    "reference_document": "GSF Spike Layer",
    "apply_to_all_doctypes": 0,
    "document_type": "Stock Entry Detail",
}).insert()
```

`document_type` is a single `Link`, not a list. With `apply_to_all_doctypes = 0`
the field landed only on `Stock Entry Detail` (plus `Stock Ledger Entry` and
`Stock Closing Balance`, which ERPNext always tags on regardless of the chosen
scope) — not on `Sales Invoice Item`, which GSF also needs tagged per gate 0d's
own chain. **There is no native "these four DocTypes" mode.** The choice is
between everything that touches stock, or exactly one DocType.

## Decision

**No application code may read a layer's dimension value as its cost.** §16 already required reading cost from
the actual SLE rather than from GSF's own layer registry; gate 0c shows this is
not caution, it is the only correct behavior, because the dimension does not
participate in valuation selection at all. The dimension exists to (a) let a
layer be traced through the ledger and (b) let ERPNext's native negative-stock
check reject overselling a specific layer — nothing more.

**The GSF Inventory Dimension keeps `apply_to_all_doctypes = 1`, and GSF's own
`after_migrate` hook deletes the custom fields it created on every DocType
outside its own domain**, immediately after ERPNext finishes registering them.
The alternative of giving the layer two different field names on two different
DocTypes (one dimension record per DocType) was rejected: it breaks the one
property gate 0d proved valuable — a single column name that traces a layer
through the entire chain from reallocation to sale. Letting the pollution stand
unremoved was rejected too: "GSF does not write foreign-domain fields" is
already the rule for behavior; leaving GSF's own field physically present on
`CC Stock Lot` violates the same rule at the schema level, and a stray column
that nothing writes to is not free — it is index and migration surface with no
owner.

The cleanup patch is a known-doctype allowlist inverted into a denylist: it
removes fields from `CC *` DocTypes (and any future non-stock DocType Frappe's
scan happens to include) rather than maintaining a positive list of DocTypes
GSF needs. This is deliberately the opposite shape from the rejected "explicit
list of several DocTypes" — that shape does not exist in the platform, this one
does, because deleting a `Custom Field` after the fact is an ordinary write, not
a mode Inventory Dimension has to support.

## Consequences

- No GSF service, report, or reconciliation may compute a cost from the layer
  registry as a substitute for the ledger. Any future code that does so is a
  defect against this ADR, not a valid optimization.
- The denylist of foreign DocTypes to strip is a maintenance surface: a future
  ERPNext or Frappe version, or a new commission DocType, can reintroduce
  pollution the patch does not yet know about. The patch must assert its own
  result — after running, zero `CC *` DocTypes carry the GSF field — rather
  than assume the list stays complete, so drift fails the migration instead of
  passing silently.
- The patch runs after ERPNext's own dimension registration in the same
  `after_migrate`, per [ADR-001](0001-stock-domain-ownership.md)'s
  requirement that dimension creation order be fixed and explicit, not
  incidental.
