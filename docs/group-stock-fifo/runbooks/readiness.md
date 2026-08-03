# Runbook: readiness

Readiness — це не формальність, а перелік станів, у яких продаж дасть невірний облік.

## Симптом

`GSF Settings.enabled` не вмикається.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_readiness
```

## Чого НЕ робити

Не вимикати перевірки й не правити `enabled` в обхід контролера.

## Виправлення

Кожен `blocking_check` називає себе. Найчастіші:
- `CLEARING_ACCOUNT_MISSING` — немає Due From/Due To або вони витратні (§15.3);
- `Selling Settings does not allow one Item on several rows` — §18.3;
- `Warehouse ... is bound to two stock domains` — `warehouse-provisioning.md`;
- `Staging lane ... is dirty` — `dirty-stage-recovery.md`.

## Перевірка

`status = ready_for_acceptance`.

## Rollback / ескалація

Попередження про scheduler і про counterparty dimension не блокують, але перше з них означає, що другий продаж дня не пройде — див. `valuation-divergence.md`.
