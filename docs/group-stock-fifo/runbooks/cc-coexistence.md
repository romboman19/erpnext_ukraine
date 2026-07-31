# Runbook: співіснування з комісійним модулем

CC і GSF живуть в одному застосунку й на одному сайті. Розділяє їх виключно `GSF Warehouse Binding` (ADR-001).

## Симптом

GSF-поле з'явилось на комісійному DocType, або GSF намагається взяти комісійний склад.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> execute frappe.client.get_list --kwargs '{"doctype":"Custom Field","filters":{"fieldname":["in",["gsf_stock_layer","to_gsf_stock_layer"]]},"fields":["dt"]}'
```

## Чого НЕ робити

Не видаляти `Inventory Dimension GSF Stock Layer` — це знесе поля з усієї книги і зробить наявні шари невідстежуваними.

## Виправлення

Патч ADR-002 прибирає чужі поля на кожному `after_migrate` і **перевіряє власний результат**. Якщо поле лишилось — міграція мала впасти; прогоніть її ще раз і читайте помилку:

```bash
docker exec frappe-test-backend-1 bench --site <site> migrate
```

## Перевірка

У списку вище не має бути жодного DocType з `erpnext_ua` — тільки складські DocType ядра ERPNext.

## Rollback / ескалація

Новий комісійний DocType зі складським полем — очікуваний випадок; патч прибере його сам. Якщо ні — це баг патча, а не конфігурації.
