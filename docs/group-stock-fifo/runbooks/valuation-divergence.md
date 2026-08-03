# Runbook: розбіжність черги оцінки

Найважливіший runbook. Гейт 0c: мітка виміру НЕ керує тим, який шар спише ERPNext.

## Симптом

`VALUATION_QUEUE_DIVERGENCE`, `PENDING_REPOST` або `UNCLASSIFIED_GSF_STOCK` при спробі підготувати продаж.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_integrity
docker exec frappe-test-backend-1 bench --site <site> execute frappe.client.get_count --kwargs '{"doctype":"Repost Item Valuation","filters":{"status":["in",["Queued","In Progress"]]}}'
```

## Чого НЕ робити

Не вимикати preflight. Він відмовляє саме тоді, коли продаж дав би невірний COGS, і мовчазний продаж коштує дорожче за зупинену касу.

## Виправлення

- `PENDING_REPOST` → **перевірте, що scheduler працює**. Кожен керований потік лишає repost, і без scheduler другий продаж дня неможливий у принципі. Це вимога розгортання, а не зручність.
- `UNCLASSIFIED_GSF_STOCK` → у пулі є запас без шару: `opening-stock-migration.md`.
- `VALUATION_QUEUE_DIVERGENCE` → у пулі лежить старіший шар, якого план не взяв. Повідомлення називає шари поіменно.

## Перевірка

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_integrity
```

## Rollback / ескалація

Розбіжність після компенсації — очікувана: повернений запас стає в кінець локальної черги, тоді як глобальна FIFO-дата шару незмінна. Дочекайтесь repost і перевірте ще раз.
