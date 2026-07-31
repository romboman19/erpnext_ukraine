# Runbook: вимкнення GSF

Вимкнення — не видалення. Дані шарів і рухів лишаються як аудит.

## Симптом

Треба зупинити GSF, не розбираючи облік.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> execute frappe.client.get_count --kwargs '{"doctype":"GSF Checkout","filters":{"status":["not in",["COMPLETED","CANCELLED","COMPENSATED","RETURNED"]]}}'
```

## Чого НЕ робити

Не вимикати гейт, поки є чеки в польоті: запас лишиться в lane, а сага більше не працюватиме, щоб його забрати.

## Виправлення

1. Доведіть або скасуйте всі активні чеки.
2. Переконайтесь, що всі lane порожні й `AVAILABLE`.
3. `GSF Settings.enabled = 0`.

Запас лишається там, де лежить: у пулах компаній-власників, з правильними шарами.

## Перевірка

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_integrity
```
Очікується `ok`.

## Rollback / ескалація

Видаляти `Inventory Dimension` не треба: без нього історія книги стає невідстежуваною, а користі від видалення немає.
