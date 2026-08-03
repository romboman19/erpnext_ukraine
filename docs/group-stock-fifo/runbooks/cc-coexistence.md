# Runbook: співіснування з комісійним модулем

CC і GSF живуть в одному застосунку й на одному сайті. Розділяє їх
`GSF Warehouse Binding` (ADR-001). Кожне збереження `CC Location`, install і
migrate синхронізують її три склади як `CC_*` / `DISCOVERED_EXTERNAL`; ці записи
read-only для логіки GSF і не передають йому право проводити CC-операції.

## Симптом

GSF-поле з'явилось на комісійному DocType, або GSF намагається взяти комісійний склад.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> execute frappe.client.get_list --kwargs '{"doctype":"Custom Field","filters":{"fieldname":["in",["gsf_stock_layer","to_gsf_stock_layer"]]},"fields":["dt"]}'
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.setup.cc_discovery.audit_cc_bindings
```

## Чого НЕ робити

Не видаляти `Inventory Dimension GSF Stock Layer` — це знесе поля з усієї книги і зробить наявні шари невідстежуваними.

## Виправлення

Патч ADR-002 прибирає чужі поля на кожному `after_migrate` і **перевіряє власний
результат**. CC discovery тим самим migrate відновлює відсутні/застарілі
read-only binding. Якщо поле лишилось або audit повертає конфлікт — прогоніть
міграцію ще раз і читайте помилку:

```bash
docker exec frappe-test-backend-1 bench --site <site> migrate
```

## Перевірка

У списку Custom Field не має бути жодного чужого DocType з `erpnext_ua` — тільки
складські DocType ядра ERPNext. `audit_cc_bindings` має повернути порожній список.

## Rollback / ескалація

Новий комісійний DocType зі складським полем — очікуваний випадок; патч прибере його сам. Якщо ні — це баг патча, а не конфігурації.
