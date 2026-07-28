# ADR 0006 — Stage lane isolation

## Status

Accepted on 2026-07-27, after gates 0c and 0f. Numbered per
[spec](../spec-v1.0.md) §40.

**Supersedes** the Sale Stage half of the ADR written before the base spec
landed, which chose a warehouse created per checkout. That choice is withdrawn
in favour of the spec's staging-lane pool — see "Why the earlier choice was
wrong" below.

## Context

§7.2 requires at minimum one Sale Stage lane per parallel checkout stream and
states plainly that **one lane cannot serve two checkouts at once**. §9.8
specifies the mechanism: a `GSF Staging Lane` DocType with `lock_token`,
`current_checkout`, `status` in `AVAILABLE / LOCKED / DIRTY / DISABLED`, and a
zero-balance check before every lock. §33 reserves `STAGE_LANE_BUSY` and
`STAGE_LANE_DIRTY`.

What the spec asserts but does not prove is why the isolation has to be that
strict. That is what Phase 0 supplied.

## Evidence

Gate 0c ([evidence](../spikes/evidence/2026-07-27-gate-0c-sale-stage-cogs.md))
sold the same prepared layer twice, changing only what else sat in the stage:

| Run | What else was in the stage | Prepared | COGS charged |
|---|---|---:|---:|
| A | nothing | 2000.00 | **2000.00** |
| B | 1 unit at 1500, a different layer | 2000.00 | **2500.00** |

Run B's invoice row carried the correct layer dimension and ERPNext still
consumed the older, unrelated unit. §10.2 predicted this; the gate priced it at
500 on a 2000 checkout.

Gate 0f ([evidence](../spikes/evidence/2026-07-27-gate-0f-timestamp-ties.md))
supplied the mechanism behind the collision: at an identical posting timestamp
ERPNext consumes whichever layer was **submitted into the warehouse first**,
deterministically. A stage shared by two in-flight checkouts therefore hands the
first invoice whatever arrived first, regardless of which checkout reserved it.

The business consequence is not only a wrong COGS. The reallocation liability
recorded against a source FOP's clearing account (ADR-005) becomes attached to
the wrong sale, so both the cost and the inter-company position land on the
wrong company.

## Decision

**Sale Stage is a pool of persistent lanes, one lane locked by at most one
checkout at a time**, exactly as §7.2 and §9.8 specify. GSF does not create a
warehouse per checkout.

- A lane is a real, long-lived `GSF_SALE_STAGE` warehouse registered in
  `GSF Warehouse Binding` and described by `GSF Staging Lane`.
- Lanes are provisioned per `consumer_type` / `consumer_reference` — a POS
  Profile, the web channel, a contract flow — so ordinary parallelism is served
  by distinct lanes rather than by contention.
- `GSF Checkout` acquires a lane by lock at the start of `PREPARING_STOCK` and
  releases it on any terminal state, including `COMPENSATED` and `FAILED`.
- **The zero-balance check is a precondition of the lock, not a formality.**
  §9.8 requires every Item in the lane to be at zero. A non-zero balance sets
  `status = DIRTY`, raises `STAGE_LANE_DIRTY` and a CRITICAL
  `GSF Integrity Issue`, and blocks the checkout (§37.11).
- A dirty lane is **never** cleaned automatically. §44 forbids it explicitly.
- If no lane is free, the checkout receives `STAGE_LANE_BUSY` (§37.10) and may
  be routed to another lane by the orchestrator.

## Why the earlier choice was wrong

Before the spec was available, this decision was made as "one warehouse per
checkout", with a lane pool recorded only as a fallback. Both designs enforce
the same invariant, but the per-checkout warehouse pays for it badly:

- it creates and discards warehouses at transaction rate, which §7.4 already
  discourages by forbidding rename and delete after the first stock movement;
- it puts transient rows into every standard warehouse report and dropdown;
- it has no place to record `DIRTY` state, because the carrier disappears with
  the checkout — and gate 0c shows dirty state is precisely what must survive
  and block the next attempt.

The lane pool keeps a stable, auditable object per stream and makes the failure
state representable. The spec was right and the earlier ADR was reasoning
without it.

## Consequences

- Lane provisioning joins warehouse provisioning in Phase 1 (§41), not Phase 4.
- `GSF Staging Lane.status` is operational state that outlives any single
  checkout; the stuck-checkout monitor and the stage zero check (§30.5) both
  read it.
- Lane granularity is a deployment decision: a location with three tills and a
  web channel needs at least four lanes per selling company. Under-provisioning
  shows up as `STAGE_LANE_BUSY`, which is a visible, non-corrupting failure.
- Readiness (§30.2) blocks activation when a lane carries a non-zero balance,
  and §30.3 warns when a company that can sell has no lane at all.
