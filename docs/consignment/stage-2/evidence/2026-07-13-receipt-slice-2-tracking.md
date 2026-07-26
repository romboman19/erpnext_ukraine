# Stage 2 receipt slice 2 tracking evidence — 2026-07-13

## Scope

Slice 2 додає Batch/Serial receipt identity та immutable ownership guard на
Stock Entry boundary. Production не використовувався.

## Receipt flow

Один integration receipt на `postest-restore.local` містив:

- auto-created Batch, quantity `2`;
- два явно задані нові Serial Nos, quantity `2`;
- один auto-created Serial No з Item series, quantity `1`.

ERPNext створив native Serial and Batch Bundles. Фактичні identities записані у
`CC Receipt Item` і `CC Stock Lot`; кожен Batch/Serial master отримав той самий
`cc_stock_lot`, що й Inventory Dimension відповідного SLE. Усі active ownership
balances дорівнювали received quantity, valuation лишилася zero.

## Guard evidence

Integration test підтвердив:

- Batch owner A з dimension owner B відхиляється;
- Serial No owner A з dimension owner B відхиляється;
- owned Batch без ownership dimension відхиляється;
- tracked transfer із різними source/target ownership coordinates відхиляється;
- established Batch owner не можна змінити через Document save;
- owned Batch master не можна видалити;
- звичайний native tracked receipt у own warehouse без ownership dimension
  проходить і лишає Batch unowned;
- cancel `CC Receipt` повертає всі ownership balances до zero, а master mapping
  зберігається як audit evidence.

Guard читає і legacy `batch_no`/`serial_no`, і modern Serial and Batch Bundle
entries. Auto identity без попереднього owner дозволена лише inward Stock Entry,
контрольовано пов'язаному з `CC Receipt`.

## Verification

- Ruff: pass;
- compileall: pass;
- isolated unit suite: `66 tests`, `OK`;
- `bench --site postest-restore.local migrate`: success;
- full application integration suite: `3 tests`, `OK`, `25.112s`.

Після integration cleanup:

- `CC Settings.enabled=0`;
- `Stock Settings.enable_serial_and_batch_no_for_item=0` (попереднє значення
  відновлене);
- integration `CC Receipt`, `CC Stock Lot` і Items: `0`.

## Remaining boundary

Цей slice не активує feature gate і не додає Sales Invoice/POS, transfer/return
або combined Serial+Batch flows. Перед tracked receipt адміністратор має
ввімкнути ERPNext `Activate Serial and Batch No for Item`.

Production не монтувався, не мігрувався, не перезапускався і не отримував
записів.
