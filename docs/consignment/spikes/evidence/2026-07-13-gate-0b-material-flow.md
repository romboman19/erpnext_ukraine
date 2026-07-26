# Gate 0B material-flow evidence — 2026-07-13

## Result

`PARTIAL PASS`: Inventory Dimension propagation and dimension-level negative
stock validation passed for Stock Entry, and propagation through Sales Invoice
with `update_stock=1` also passed. Gate 0B remains open for Serial and Batch
Bundle, transfers, returns, reconciliation and performance.

## Isolation

- compose project: `frappe-test`;
- site: `postest.local`;
- company: `POS Test Ukraine`;
- material-flow runner commit: `941fe5e` on `agent/stage-0-scaffold`;
- Sales Invoice runner commit: `ae7c55c` on `agent/stage-0-scaffold`;
- production compose project `frappe` was not changed or restarted.

The runner rejects every site except the explicit `postest.local` allow-list
entry and also requires the confirmation value `RUN_GATE_0B`.

## Fixture

The following records intentionally remain on the test site for subsequent
Stage 0 probes:

| Type | Value |
|---|---|
| Reference DocType | `TP Spike Lot` |
| Inventory Dimension | `TP Spike Lot` / `tp_spike_lot` |
| Dimension value | `TP-GATE-0B-LOT-001` |
| Item | `TP-GATE-0B-ZERO-VALUE-ITEM` |
| Warehouse | `TP Gate 0B Warehouse - PTU` |

The Inventory Dimension applies to all inventory documents and has native
dimension-level negative stock validation enabled.

## Material flow

| Step | Dimension | Qty | Dimension balance | Outcome |
|---|---|---:|---:|---|
| Material Receipt | `TP-GATE-0B-LOT-001` | +10 | 10 | submitted |
| Material Receipt | not set | +100 | 10 | submitted |
| Material Issue | `TP-GATE-0B-LOT-001` | -4 | 6 | submitted |
| Material Issue | `TP-GATE-0B-LOT-001` | -7 | 6 | rejected |

The rejected issue had enough total warehouse stock (`106`) but only `6`
units for the selected dimension. ERPNext raised
`InventoryDimensionNegativeStockError`, reporting a shortage of one unit for
that exact dimension value.

## Stock Ledger evidence

Before cleanup, the three submitted Stock Entries produced:

| Actual qty | `tp_spike_lot` | Valuation rate | Stock value difference |
|---:|---|---:|---:|
| +10 | `TP-GATE-0B-LOT-001` | 0 | 0 |
| +100 | not set | 0 | 0 |
| -4 | `TP-GATE-0B-LOT-001` | 0 | 0 |

This confirms propagation from `Stock Entry Detail.to_tp_spike_lot` for inward
stock and `Stock Entry Detail.tp_spike_lot` for outward stock into
`Stock Ledger Entry.tp_spike_lot`.

## Sales Invoice flow

A second isolated run received `10` zero-valued units for the same dimension
and submitted Sales Invoice `ACC-SINV-2026-00007` with `update_stock=1`, qty
`4` and sales rate `100` UAH.

The invoice created one active Stock Ledger Entry before cleanup:

| Actual qty | `tp_spike_lot` | Valuation rate | Stock value difference |
|---:|---|---:|---:|
| -4 | `TP-GATE-0B-LOT-001` | 0 | 0 |

The dimension balance became `6`. The invoice's active GL evidence contained
only:

| Account | Debit | Credit |
|---|---:|---:|
| `Debtors - PTU` | 400 | 0 |
| `Sales - PTU` | 0 | 400 |

There were no non-zero entries against `Cost of Goods Sold - PTU` or
`Stock In Hand - PTU`. This confirms zero COGS for this zero-valued,
dimensioned third-party stock scenario.

## Balance API finding

The true active balance for the dimension value, calculated as the sum of its
active SLE `actual_qty`, was `6`. ERPNext's
`get_stock_balance(..., inventory_dimensions_dict=...)` returned `106` in the
same state.

Source inspection explains the difference: the helper filters the prior SLE
by dimension but returns that row's warehouse-wide `qty_after_transaction`.
It must not be used as the ownership-lot balance API for mixed dimensioned and
unassigned stock. See ADR 0002.

## Cleanup

All three material-flow Stock Entries were cancelled in reverse order. The
Sales Invoice run also cancelled its invoice first and its receipt second. The
active dimension balance after each cleanup was `0`. Cancelled records remain
as audit evidence on the test site; there is no active stock from either run.

## Reproduction

```bash
bench --site postest.local execute \
  erpnext_consignment_and_commission.consignment_and_commission.spikes.inventory_dimension.run_material_flow \
  --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0B","company":"POS Test Ukraine"}'

bench --site postest.local execute \
  erpnext_consignment_and_commission.consignment_and_commission.spikes.inventory_dimension.run_sales_invoice_flow \
  --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0B","company":"POS Test Ukraine"}'
```

## Open items

1. Serial and Batch Bundle propagation and owner consistency.
2. Material Transfer, Return and Stock Reconciliation.
3. Indexed dimension-balance query and report performance at representative
   ledger volume.
