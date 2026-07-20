# Stage 2 receipt slice 1 evidence — 2026-07-13

## Scope

Перший Stage 2 slice реалізує лише приймання нетрекінгового стороннього stock у
production Inventory Dimension. Production site не використовувався.

## Restore rehearsal

На `postest-restore.local` успішно виконано `bench migrate`. Readiness повернув:

- `stage=2`;
- `status=ready_for_receipt_configuration`;
- zero blocking checks;
- наявні `CC Receipt`, `CC Receipt Item`, `CC Stock Lot`;
- наявні Inventory Dimension `CC Stock Lot`, audit custom fields і composite
  SLE index `cc_stock_lot_balance`.

## Transaction evidence

Повний integration-набір Stage 1+2 пройшов на повторно використаному restore
site. Receipt test додатково повторено з двома рядками одного Item:

- один submitted zero-value Material Receipt;
- дві різні `CC Stock Lot`;
- дві active SLE з `actual_qty=3` та `actual_qty=2`;
- `valuation_rate=0` і `stock_value_difference=0` для обох;
- dimension balance кожної lot дорівнював її received quantity;
- direct Stock Entry cancel був відхилений;
- cancel через `CC Receipt` скасував Stock Entry, перевів обидві lots у
  `CANCELLED` і повернув обидва dimension balances до zero.

Integration run: `2 tests`, `OK`; повторний багаторядковий receipt run:
`1 test`, `OK`.

## Safety boundary

- `CC Settings.enabled` не активується міграцією;
- Serial/Batch Items відхиляються;
- native Stock Entry без `cc_receipt` не змінює поведінку;
- production не мігрувався і транзакції на ньому не створювалися.

## GitHub and test-site rollout

- implementation head `c59476e9e16ccbc632686ee9306b8ec75ca9fbd5`;
- GitHub `CI` run `29277907034`: success;
- clean-site Frappe Integration run `29277906986`, job `86911511259`: success
  in 3m38s, including both Stage 1 and Stage 2 integration tests;
- two initial clean-site failures exposed missing Setup Wizard fixtures for
  standard Material Receipt Stock Entry Type and the current Fiscal Year; the
  production service now resolves Stock Entry Type by purpose, while the test
  suite creates missing ERPNext setup records only inside its fixture;
- test-server checkout fast-forwarded cleanly to `c59476e`, with ownership
  `romboman19:romboman19` preserved;
- Docker mount audit confirmed the checkout is mounted only in
  `frappe-test-backend-1`; `frappe-backend-1` has no application-source mount;
- `bench --site postest.local migrate`: success, including `after_migrate`;
- post-restart readiness: Stage 2, `ready_for_receipt_configuration`, zero
  blocking checks;
- `CC Settings.enabled=0`; both bootstrap Contracts remain `DRAFT`; no
  `CC Receipt` or `CC Stock Lot` transaction records were created on
  `postest.local`;
- only `frappe-test-backend-1`, `frappe-test-worker-1` and
  `frappe-test-scheduler-1` were restarted; all returned `Up`, one worker was
  online and no jobs were queued.

Production was not mounted, migrated, restarted or written during this slice.
