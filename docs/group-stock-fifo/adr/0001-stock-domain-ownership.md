# ADR 0001 — Domain ownership and warehouse binding

## Status

Accepted on 2026-07-27, after gates 0g and 0d.

## Context

Consolidation ([CC ADR 0009](../../consignment/adr/0009-single-app-consolidation.md))
removed the package boundary that used to separate the commission domain from
everything else. The GSF revision states that the `GSF` prefix, not the package
edge, now separates the domains — but a prefix is a naming convention, not an
enforcement mechanism. Something has to actually stop a GSF code path from
consuming commission stock and vice versa.

## Evidence

Gate 0g ([evidence](../spikes/evidence/2026-07-27-gate-0g-shared-allocator.md))
tested this directly, because both domains now feed the same allocator.

Test 4 showed the isolation works as intended: a commission adapter present in
the same allocation run contributes nothing when its warehouse is absent from
`allowed_warehouses`, even though its lot is older than every GSF layer and
would otherwise win the FIFO run outright.

Test 5 is the one that decides this ADR. It is the negative control: widen
`allowed_warehouses` by a single entry and the allocator faithfully hands a
commission lot into a GSF plan. There is no second line of defence. The GSF
planner then fails — but on a bare `KeyError` from a dictionary lookup, because
it cannot resolve an owner for a layer it does not know.

Gate 0d ([evidence](../spikes/evidence/2026-07-27-gate-0d-layer-dimension.md))
added a second, quieter breach: registering the GSF dimension with
`apply_to_all_doctypes = 1` created eight custom fields on commission DocTypes
(`CC Stock Lot`, `CC Allocation`, `CC Receipt Item` and five more). The
commission dimension has none on the GSF carrier, purely because it was created
first. Schema ownership currently depends on installation order.

## Decision

**`GSF Warehouse Binding` is the single authoritative registry of which
technical warehouse belongs to the GSF domain.** No other code path may
assemble an `allowed_warehouses` set. A binding maps physical location and
owning company to one technical warehouse.

**A warehouse belongs to at most one stock domain.** This is an invariant with
its own validation, not a convention: creating a GSF binding for a warehouse
that already carries commission lots, or that is registered as a `CC Location`
warehouse, is rejected at insert time. A regression test asserts both
directions.

**A candidate adapter must reject material it does not own with a typed domain
error.** The bare `KeyError` observed in gate 0g is not acceptable as the
failure mode for a cross-domain leak: it fails closed but reports nothing
actionable. GSF raises a dedicated error naming the foreign layer and the
binding that should have excluded it.

**GSF never writes `cc_*` fields and never inserts or mutates `CC Stock Lot` or
`CC Allocation`.** Enforced by test rather than by import restrictions, as the
revision already established — there is no package boundary left to lean on.

**Dimension registration order is fixed and explicit.** Both dimensions are
created from one `after_migrate` in a defined order, so two installations of the
same app version converge on the same schema. Whether GSF should use
`apply_to_all_doctypes` at all, or an explicit list of stock DocTypes that
excludes the other domain's tables, remains open in
[ADR-002](0002-inventory-dimension-coexistence.md); this ADR only
requires that the order stop being incidental.

## Consequences

- `GSF Warehouse Binding` becomes a Phase 1 prerequisite, not a convenience
  DocType — nothing else can compute an allocation scope without it.
- The binding registry needs its own diagnostics entry: an unbound warehouse
  holding GSF layers, or a warehouse bound to both domains, is a
  fail-closed condition that operators must be able to see before a checkout
  hits it.
- Cross-domain isolation is testable at the service level today (gate 0g) but
  the hook level cannot be tested until GSF hooks exist. Gate 0i stays open for
  that reason, and is not a blocker for Phase 1 start.
- Because enforcement is a test rather than a package boundary, the test suite
  is load-bearing. Removing the isolation tests silently re-opens the leak.
