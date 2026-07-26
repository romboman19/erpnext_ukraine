# Gate 0C global FIFO and reservation evidence — 2026-07-13

## Result

`PASS` for technical warehouse isolation, zero-valued third-party stock,
global FIFO allocation, mixed Sales Invoice valuation, cancel/reuse and
last-unit concurrency.

## Isolation

- compose project: `frappe-test`;
- site: `postest.local`;
- company: `POS Test Ukraine`;
- runner commit: `9828840` on `agent/stage-0-scaffold`;
- production compose project `frappe` was not changed or restarted.

## Domain service

`AllocationService` is a Frappe-independent Python service. Eleven isolated
tests now run in CI, including six allocation tests for:

- global FIFO across all three models and warehouses;
- stable receipt/row/lot tie-breaking;
- exact Serial No priority;
- Batch filtering;
- reserved quantity exclusion;
- controlled insufficient stock.

The resolver applies the TЗ priority exactly: Serial, Batch, then global FIFO
by receipt timestamp with stable tie-breakers.

## Technical warehouses

The runner created one group for the physical location and three leaves:

| Type | Warehouse |
|---|---|
| OWN | `TP Gate 0C Own - PTU` |
| COMMISSION | `TP Gate 0C Commission - PTU` |
| CONSIGNMENT | `TP Gate 0C Consignment - PTU` |

Each leaf carries a test-only read-only type field. The cashier-facing resolver
received the location and allowed warehouse set; it did not expose warehouse
selection as user input.

## Receipt and FIFO evidence

The same Item was received as three distinct ownership lots:

| FIFO position | Model | Timestamp | Qty | Valuation | Stock value change |
|---:|---|---|---:|---:|---:|
| 1 | Commission | 2026-07-13 08:00 | 2 | 0 | 0 |
| 2 | Own | 2026-07-13 09:00 | 2 | 50 | +100 |
| 3 | Consignment | 2026-07-13 10:00 | 2 | 0 | 0 |

A request for five units produced this preview:

| Sequence | Model | Qty |
|---:|---|---:|
| 1 | Commission | 2 |
| 2 | Own | 2 |
| 3 | Consignment | 1 |

This proves global ordering across warehouses rather than three independent
warehouse FIFO decisions.

## Split Sales Invoice

Sales Invoice `ACC-SINV-2026-00010` was generated from the allocation slices.
Its SLE evidence was:

| Model | Actual qty | Valuation rate | Stock value difference |
|---|---:|---:|---:|
| Commission | -2 | 0 | 0 |
| Own | -2 | 50 | -100 |
| Consignment | -1 | 0 | 0 |

GL contained Debtors/Sales for `500` and Stock In Hand/COGS for `100`. The
`100` stock value came exclusively from the Own warehouse; commission and
consignment did not affect stock asset or standard COGS.

## Cancel and future reuse

The first Sales Invoice was cancelled. A fresh allocation request then selected
the oldest Commission lot again, submitted a new one-unit Sales Invoice and
created a correct zero-valued outward SLE. After its cancellation, all three
lot balances were restored to `2`.

This confirms that cancellation/reposting did not poison subsequent FIFO
selection or future SLE creation.

## Concurrent last-unit reservation

The test-only reservation row started with `available_qty=1` and
`reserved_qty=0`. Two independent `bench execute` processes were launched in
parallel and ran the same conditional update after a shared two-second delay.

| Contender | Affected rows | Success | Observed reserved qty |
|---|---:|---|---:|
| POS-A | 0 | no | 1 |
| POS-B | 1 | yes | 1 |

Exactly one checkout acquired the last unit. The losing checkout received a
controlled conflict and oversell did not occur. The reservation was released
after the probe; persistent test fixture state is `reserved_qty=0`.

## Cleanup

- both Sales Invoices were cancelled;
- all three receipt Stock Entries were cancelled;
- Own, Commission and Consignment active balances are all `0`;
- the concurrency reservation is released;
- fixture warehouses, lots and test-only reservation DocType remain available
  for later POS saga probes.

## Reproduction

```bash
bench --site postest.local execute \
  erpnext_consignment_and_commission.consignment_and_commission.spikes.fifo.run_global_fifo_flow \
  --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0B","company":"POS Test Ukraine"}'
```

The concurrency proof additionally uses `prepare_last_unit_probe`, two parallel
calls to `attempt_last_unit_reservation`, and `cleanup_last_unit_probe` from the
same module.
