# Runbook: резервна копія й відновлення

GSF не має власного стану поза сайтом: усе в тій самій базі.

## Симптом

Потрібно зняти або відновити копію сайту з увімкненим GSF.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> execute frappe.client.get_count --kwargs '{"doctype":"GSF Checkout","filters":{"status":["not in",["COMPLETED","CANCELLED","COMPENSATED","RETURNED"]]}}'
```

## Чого НЕ робити

Не знімати копію під час активного чека: у ній буде запас у lane без завершеного продажу, і після відновлення це буде брудна lane.

## Виправлення

```bash
docker exec frappe-test-backend-1 bench --site <site> backup --with-files
```
Перед копією дочекайтесь, поки лічильник вище стане нулем.

## Перевірка

Після відновлення: `docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_readiness` і `docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_integrity`.

## Rollback / ескалація

На відновленій копії feature gate краще тримати вимкненим, доки не звірено цілісність — інакше каса почне продавати проти застарілого стану.
