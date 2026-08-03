# ADR 0003 — One allocator, two candidate adapters

## Status

Accepted on 2026-07-27, after gates 0g, 0f and 0j.

## Context

The base spec assumes GSF needs its own FIFO allocator, on the grounds that its
scope is `group + physical_location + item` while the commission allocator works
on `item + company + cc_location`. The revision was sceptical and asked for the
impossibility to be proved before a second allocator got written, but did not
answer the question.

## Evidence

Gate 0g ([evidence](../spikes/evidence/2026-07-27-gate-0g-shared-allocator.md))
answered it against the base spec. `allocate_global_fifo` takes no company
argument at all:

```python
def allocate_global_fifo(candidates, *, item_code, location, qty, allowed_warehouses, ...)
```

The entire scope difference lives in the adapter that produces candidates, not
in the allocation rule. `preview_from_adapters` already accepts a
`Sequence[CandidateAdapter]` and merges them into one deterministic run, which
is exactly the extension point a second domain needs. Six tests covered
cross-company FIFO with no seller priority, reallocation marking, domain
isolation, and stable ordering when two layers share a timestamp.

Gate 0j ([evidence](../spikes/evidence/2026-07-27-gate-0j-end-to-end.md))
carried this from unit test to site. The real allocator planned a three-company
scenario — 2 units at 1000, 3 at 1100, 1 at 1200 — from unit costs read back out
of the stock ledger, and ERPNext charged exactly the planned 6500. The seller's
own layer went last, so the §4 ban on seller-first FIFO holds on real data.

Gate 0f ([evidence](../spikes/evidence/2026-07-27-gate-0f-timestamp-ties.md))
constrains how the plan may be executed: at an identical posting timestamp
ERPNext consumes whichever layer was submitted into the warehouse first,
deterministically across three runs.

## Decision

**GSF does not get its own allocator.** It ships a `GSF Layer` candidate
adapter alongside `candidates_from_cc_stock_lot`, and both feed
`allocate_global_fifo` through `preview_from_adapters`.

**One additive change to the shared allocation module is permitted:**
registering `GSF_LAYER → OWN` in `SOURCE_METHOD_RELATIONSHIP_MODEL`. GSF layers
are owned stock of exactly one company; the spike borrowed `BUYOUT` to avoid
touching shared code before this ADR existed. Nothing else in `allocation.py`
changes.

**`CandidateQuery.company` means "the requesting company", and each adapter
decides what that implies.** For the commission domain it is a stock filter —
only that company's lots are eligible. For GSF it is the *seller*, and it
filters nothing: a slice owned by any other company in the pool is precisely
what triggers reallocation. The field is renamed to `requesting_company` with
both meanings documented at the definition. Leaving one name silently carrying
two semantics is the cheap option now and an expensive bug later.

**Reallocation documents are submitted in the allocator's sequence order.**
Gate 0f makes submission order the tie-breaker inside a warehouse, so the order
GSF chooses must be the order it submits. This is a hard requirement on the
execution path, not an implementation preference.

## Consequences

- The weeks budgeted for a second allocator are not needed. The GSF adapter is
  a snapshot-to-candidate mapping plus an owner lookup.
- The shared allocator gains a second consumer, so its test suite becomes
  load-bearing for two domains. Changes to `_fifo_key` or eligibility filtering
  now affect commission trade and multi-FOP trade together.
- Renaming `CandidateQuery.company` touches commission code that is already in
  release. It is a mechanical rename with no behaviour change, but it must land
  as its own commit with the commission tests green.
- The adapter must resolve a layer's owning company itself; the allocator
  returns layer identity only, and deliberately knows nothing about ownership.
- Sub-second posting-time offsets, floated as a safeguard while 0f was
  unanswered, are not adopted. They would have distorted reporting to solve a
  problem that submission order already solves.
