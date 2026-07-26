# Gate 0B dimension-balance performance — 2026-07-13

## Result

`PASS WITH REQUIRED COMPOSITE INDEX`.

The exact SLE aggregate needed for ownership availability was benchmarked on
the real test schema and on a temporary 250,000-row InnoDB dataset. A
dimension-only index is insufficient; a composite lookup index reduced the
optimizer estimate from `21,072` rows to `1` and improved median latency by
approximately `53x`.

## Isolation and method

- compose project: `frappe-test`;
- site: `postest.local`;
- MariaDB: 11.8;
- runner commit: `cdc5854` on `agent/stage-0-scaffold`;
- 30 timed executions after three warm-up executions per index;
- production compose project `frappe` was not queried or changed.

The benchmark created a connection-scoped temporary InnoDB table, populated it
from MariaDB's sequence table, ran both index variants, and dropped the table
in `finally`. `temporary_table_removed` returned `true`.

## Query shape

```sql
SELECT COALESCE(SUM(actual_qty), 0)
FROM `tabStock Ledger Entry`
WHERE item_code = ?
  AND warehouse = ?
  AND ownership_lot = ?
  AND is_cancelled = 0;
```

The benchmark column was named `owner_lot`; the test site's actual Inventory
Dimension column was `tp_spike_lot`. `ownership_lot` above denotes the final
production field name to be selected with the DocType model.

## Actual test SLE baseline

The test site contained:

- total SLE rows: `52`;
- SLE rows with `tp_spike_lot`: `44`.

Available relevant indexes were:

1. ERPNext's composite
   `(item_code, warehouse, posting_datetime, creation)`;
2. Inventory Dimension's single-column `tp_spike_lot_index`.

MariaDB combined both through a rowid filter and estimated `28` rows. On this
small dataset, the active-balance aggregate had median `0.3881 ms` and p95
`0.4663 ms`. This is a development baseline only, not a capacity result.

## Representative temporary dataset

| Index | Estimated rows | Median | p95 | Max |
|---|---:|---:|---:|---:|
| `(owner_lot)` | 21,072 | 17.1121 ms | 17.3470 ms | 17.5918 ms |
| `(item_code, warehouse, owner_lot, is_cancelled)` | 1 | 0.3208 ms | 0.3682 ms | 0.3758 ms |

Both variants returned the same balance. The composite index produced a
`21,072x` reduction in estimated examined rows and approximately `53.3x`
improvement in median latency.

## Decision

The application must add a deterministic composite SLE index after the final
ownership Inventory Dimension field exists:

```text
(item_code, warehouse, ownership_lot, is_cancelled)
```

The migration must be idempotent and verify the index definition, not only its
name. The balance service must use an explicit aggregate of active SLE
`actual_qty`; ERPNext's `get_stock_balance(...inventory_dimensions_dict=...)`
is not a correct ownership balance for mixed stock.

Global FIFO queries that span multiple warehouses require a separate EXPLAIN
and may need an ordering-oriented index including posting datetime and
creation. That optimization belongs to Gate 0C and must not overload the exact
warehouse-balance index with conflicting responsibilities.

## Reproduction

```bash
bench --site postest.local execute \
  erpnext_consignment_and_commission.consignment_and_commission.spikes.performance.run_dimension_balance_benchmark \
  --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0B","company":"POS Test Ukraine"}'
```
