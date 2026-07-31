# Runbook: розбіжність clearing-рахунків

Due From однієї компанії має дорівнювати Due To іншої по кожному перепризначенню. Інакше книги групи не сходяться самі з собою.

## Симптом

`CLEARING_IMBALANCE` у звіті цілісності.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_integrity
```

## Чого НЕ робити

Не «підганяти» різницю ручним Journal Entry, доки не з'ясовано причину. Проводка сховає розбіжність, але не усуне її.

## Виправлення

`GSF Reallocation Leg` містить обидва числа й обидва рахунки — це ключ звірки (амендмент ADR-005). Порівняйте `source_stock_value` і `destination_stock_value` ноги з фактичними SLE її документів. Розбіжність означає, що destination receipt створено не на ту суму, а це вже баг, а не конфігурація.

## Перевірка

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_integrity
```
`CLEARING_IMBALANCE` має зникнути.

## Rollback / ескалація

Не закривайте період із `CLEARING_IMBALANCE`: `close_period` відмовить, і це правильно (§25.4).
