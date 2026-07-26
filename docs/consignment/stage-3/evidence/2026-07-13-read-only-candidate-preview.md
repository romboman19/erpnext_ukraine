# Stage 3 read-only candidate preview evidence — 2026-07-13

## Scope

Цей slice з'єднує production `CC Stock Lot`/Stock Ledger дані з domain global
FIFO allocator. Він не створює reservation, Sales Invoice, POS Order або інші
транзакційні документи.

## Adapter boundary

`CCStockLotCandidateAdapter`:

- фільтрує Company, Location, Item, дозволений Warehouse і lot status;
- групує активні Stock Ledger Entry за `cc_stock_lot` та Warehouse;
- використовує receipt datetime/name/row/lot як stable FIFO coordinates;
- читає fiscal policy з пов'язаного Contract;
- звіряє Serial No Warehouse та immutable `cc_stock_lot` master mapping;
- відхиляє неточний Serial balance або aggregate Serial reservation без
  identity-level reservation records.

Adapter повертає тільки candidates. `preview_from_adapters` об'єднує його з
майбутнім OWN adapter перед викликом одного `allocate_global_fifo`.

## Verification

- Ruff: pass;
- compileall: pass;
- isolated suite: `72 tests`, `OK`;
- focused allocation/candidate tests у Frappe v16 container: `12 tests`, `OK`;
- `postest-restore.local` application integration: `3 tests`, `OK`, `25.885s`.

Integration receipt preview підтвердив:

- untracked quantity `4` розподілилася між двома real lots як `3 + 1`;
- Batch filter вибрав точну Batch ownership lot;
- explicit Serial filter вибрав точний Serial No та його ownership lot;
- чинні receipt, cancellation і ownership guard тести не отримали регресій.

## Remaining boundary

OWN procurement candidate adapter ще відсутній, тому публічний preview API не
відкрито. Наступний slice має визначити durable source snapshot для
`BUYOUT`/`DEFERRED_PURCHASE`, після чого можна додавати atomic reservation та
preview/confirm API.

Feature gate лишається вимкненим. Production не монтувався, не мігрувався, не
перезапускався і не отримував записів.
