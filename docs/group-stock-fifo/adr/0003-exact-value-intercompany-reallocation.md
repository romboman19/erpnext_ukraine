# ADR 0003 — Exact-value intercompany reallocation

## Status

Accepted on 2026-07-27, after gates 0b and 0c. Numbered per
[spec](../spec-v1.0.md) §40.

Extracted from the merged reallocation ADR written before the base spec landed
in the repository; the evidence and the decision are unchanged.

## Context

§4.3 requires the value a source company releases to equal the value the target
company accepts, compared on **total** value rather than a rounded unit rate.
§16.1 forbids six tempting sources for that number — last purchase rate, Item
valuation rate before submit, Price List, group average, sale price, and the
rate on the origin document ignoring revaluation.

The question is whether ERPNext can be driven to accept an externally supplied
exact value on the destination receipt, and whether that value survives
rounding.

## Evidence

Gate 0b ([evidence](../spikes/evidence/2026-07-27-gate-0b-exact-value-transfer.md))
ran the transfer twice.

**Run A — contaminated target.** The destination warehouse was deliberately
loaded with one unit at 1500 before a layer worth 1000 per unit arrived. If
ERPNext had recomputed the incoming rate from the target's own valuation, the
receipt would have landed at 3000.

| Document | Warehouse | Qty | `stock_value_difference` |
|---|---|---:|---:|
| issue | Пул - ФКРВ | −2 | **−2000.00** |
| receipt | Комплектування - ФКІВ | +2 | **+2000.00** |

`delta = 0.00`. The implied incoming rate was 1000, not 1500.

**Run B — non-dividing unit cost.** A layer of three units at 1000/3, moving
two. ERPNext released 666.67 rather than the unrounded 666.6667 — and applied
**the same rounding to both documents**, so `delta = 0.00` again and the
remaining unit kept 333.33.

Gate 0c ([evidence](../spikes/evidence/2026-07-27-gate-0c-sale-stage-cogs.md))
supplied the negative half: the inventory dimension does not steer which layer a
sale draws its cost from, so the layer registry cannot be the source of the
number either.

## Decision

**The destination receipt rate is derived from the source issue's actual
`stock_value_difference`, divided by the moved quantity.** Read after submit,
keyed by `voucher_type`, `voucher_no`, `voucher_detail_no`, `item_code`,
`warehouse` and the layer dimension, exactly as §16.2 specifies.

**GSF must not pre-round.** Gate 0b shows the platform applies identical
rounding to both legs only when it is allowed to do the rounding itself. Handing
a pre-rounded rate breaks that symmetry and reintroduces the drift §4.3 forbids.

**Equality is asserted on total value, not unit rate**, per §16.2 and §16.3. The
last row leg may carry the rounding remainder.

**The layer registry is never a valuation source.** §4.3 already says
`GSF Stock Layer` is not a parallel valuation ledger; gate 0c makes that a
correctness requirement rather than a stylistic one, because the dimension
takes no part in valuation selection.

## Consequences

- The reallocation is necessarily two-phase: submit the issue, read its ledger,
  then build the receipt. It cannot be assembled in one pass from planned
  values.
- `reserved_stock_value_snapshot` on `GSF Allocation Slice` (§9.13) stays
  informational, as the spec already states. Any code that treats it as final is
  a defect against this ADR.
- §16.2's tolerance parameter exists but the observed behaviour is exact
  equality. The tolerance should be treated as a tripwire that raises
  `TRANSFER_VALUE_MISMATCH`, not as an accepted operating margin.
- Moving Average valuation was not tested and is out of scope; GSF assumes FIFO.
