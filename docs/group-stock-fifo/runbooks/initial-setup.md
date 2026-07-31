# Runbook: первинне налаштування

Порядок має значення: кожен крок спирається на попередній, і readiness перевіряє саме цей порядок.

## Симптом

`readiness` повертає `blocked`, і незрозуміло, з чого почати.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_readiness
```

## Чого НЕ робити

Не вмикати `GSF Settings.enabled` вручну через SQL. Контролер відмовляє свідомо (§30.1), і обхід означає роботу з незавершеною конфігурацією.

## Виправлення

1. `GSF Company Group` з учасниками; у кожного учасника — обидва clearing-рахунки (ADR-005), обов'язково **балансові**, не витратні.
2. `GSF Physical Location`.
3. Технічні склади й `GSF Warehouse Binding` — див. `warehouse-provisioning.md`.
4. `GSF Location Company Binding` на кожну компанію.
5. `GSF Staging Lane` для кожної компанії-продавця.
6. `Selling Settings.allow_multiple_items = 1` — свідомо, з поміткою в аудиті (§18.3, §44).
7. Аж тепер `GSF Settings.enabled = 1`.

## Перевірка

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_readiness
```
Очікується `ready_for_acceptance` і порожній `blocking_checks`.

## Rollback / ескалація

Warnings можна лишити: вони не блокують. Blocking checks обходити не можна — кожен із них означає стан, у якому продаж дасть невірний облік.
