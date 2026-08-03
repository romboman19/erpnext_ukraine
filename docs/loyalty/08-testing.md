# Testing

Тести виконуються лише на `postest.local`:

```bash
bench --site postest.local run-tests --app erpnext_ua --module erpnext_ua.ua_loyalty.tests.test_domain
bench --site postest.local run-tests --app erpnext_ua --module erpnext_ua.ua_loyalty.tests.test_services
```

Покриті P0 інваріанти: thresholds/rate-before-sale, cash-only earn base, stable allocation, final residual, over-return guard, negative balance, debt offset, idempotency conflict, unique account, expiry без debt, pending activation, reservation race/stale quote, card audit, dual approval, negative opening import, реальний Sales Invoice sale/return hook, два часткові returns і spent-earn debt.

Перед merge додатково запускаються всі `ua_pos`, `ua_fiscal`, `group_stock_fifo` та consignment integration tests. Feature flag повертається у OFF після rollback тестової транзакції.
