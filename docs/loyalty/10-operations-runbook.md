# Operations runbook

## Щоденний контроль

- звіти `UA Loyalty Negative Balances`, `Reservations`, `Reconciliation`;
- `PAYMENT_IN_PROGRESS` довше payment TTL;
- mismatch cache vs ledgers;
- failed activation/expiry scheduler logs;
- POS orders у `Payment Unknown` або `Manual Review`.

## Payment unknown

Не release reservation і не повторювати terminal sale наосліп. Спочатку reconcile terminal attempt. Якщо payment confirmed — відновити Sales Invoice posting з тим самим order/idempotency key. Якщо definitively failed — manager release.

## Reconciliation mismatch

Зняти snapshot account/ledger/reservations, виконати `reconcile(repair=0)`, знайти причину. `repair=1` дозволений лише після погодження і не створює заднім числом business movements.

## Rollback

Встановити `enabled=0`, `execution_mode=DISABLED`; не видаляти ledger, custom fields чи snapshots. Завершити/звільнити активні reservations за відомим payment state. Повернення вже loyalty-posted продажів продовжувати через модуль або контрольований manual process — не змішувати зі стандартним ERPNext Loyalty.
