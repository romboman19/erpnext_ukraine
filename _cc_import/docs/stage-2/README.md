# Stage 2 — receipt and stock

## Slice 1: receipt and ownership foundation

Реалізовано production-shaped, але feature-gated контур приймання стороннього
товару:

- `CC Receipt` і його рядки виводять Company, Location, Supplier, currency,
  relationship model та технічний Warehouse з Active `CC Contract`;
- submit створює один zero-value `Material Receipt` і окремий immutable
  `CC Stock Lot` для кожного рядка;
- Inventory Dimension `CC Stock Lot` переносить ownership у Stock Ledger Entry;
- баланс lot рахується як сума активних SLE, а не через warehouse-level
  `qty_after_transaction`;
- індекс `(item_code, warehouse, cc_stock_lot, is_cancelled)` підтримує цей
  balance query;
- Stock Entry не можна скасувати напряму; cancel `CC Receipt` каскадно скасовує
  пов'язаний Stock Entry, зберігає audit links і переводить усі lots у
  `CANCELLED`;
- readiness API перевіряє DocTypes, Inventory Dimension, custom fields та
  composite index.

## Slice 2: Batch/Serial ownership

- receipt row визначає `NONE`, `BATCH` або `SERIAL` із Item master;
- Batch може бути існуючим unowned master або створюватися нативною ERPNext
  series; Serial Nos можна задати явно або створити з Item series;
- після submit фактичні identities читаються з native Serial and Batch Bundle,
  записуються у receipt/lot audit fields і отримують immutable `cc_stock_lot`;
- Stock Entry pre-submit guard звіряє legacy fields та bundle entries з
  ownership dimension, забороняє silent auto-pick для outward third-party stock
  і відхиляє tracked identity без відповідного lot;
- established owner не можна змінити або видалити разом із Batch/Serial master;
- native tracked Stock Entry без ownership lot лишається без змін.

ERPNext Stock Settings → `Activate Serial and Batch No for Item` має бути
ввімкнено перед tracked receipt. Readiness показує цю опцію як non-blocking
configuration check, оскільки нетрекінгове приймання працює без неї.

## Межі slices 1–2

- `CC Settings.enabled` лишається вимкненим після міграції;
- приймаються лише enabled stock Items у їх stock UOM;
- Item з одночасно ввімкненими Batch і Serial та alternate UOM ще відхиляється;
- існуючий Batch можна вперше прив'язати до lot, але не перепризначити іншому;
- guard цього slice підключений до Stock Entry; Sales Invoice/POS tracked
  boundary ще не реалізований;
- немає allocation, Price Version, transfers/returns,
  settlement, payments, print forms або production rollout;
- Stock Entry hooks є раннім no-op для нетрекінгових Items і tracked masters без
  ownership.

## Verification

Unit tests покривають contract/date/model, Item/UOM/quantity, lot і tracking
invariants. Frappe integration покриває нетрекінгове та tracked приймання,
native auto Batch, explicit/auto Serial, master ownership, cross-owner Batch і
Serial rejection, immutable edit/delete, native non-owned regression та cancel.

Evidence: [`slice 1`](evidence/2026-07-13-receipt-slice-1.md),
[`slice 2`](evidence/2026-07-13-receipt-slice-2-tracking.md).
