# Runbook: перенесення початкових залишків

`GSF Opening Stock Import` (§38.2) НЕ реалізований. Це свідомо: його форма залежить від того, як власник вивантажить наявні залишки.

## Симптом

Треба завести в GSF запас, який уже фізично лежить на складі.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> execute frappe.client.get_count --kwargs '{"doctype":"GSF Stock Layer"}'
```

## Чого НЕ робити

Не заводити залишок звичайним Stock Entry у GSF-пул: §17.3 його відхилить, а якщо гейт вимкнено — створиться запас без шару, і перший же продаж дасть `UNCLASSIFIED_GSF_STOCK`.

## Виправлення

До появи імпорту: заводьте залишки керованим `Stock Entry` Material Receipt із `gsf_managed = 1`, по одному документу на компанію, з **реальною датою надходження** у `posting_date` — вона стає глобальною FIFO-датою шару й змінити її потім не можна (§9.9).

## Перевірка

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_integrity
```
Очікується `ok`; будь-який `UNCLASSIFIED_GSF_STOCK` означає запас без шару.

## Rollback / ескалація

Якщо залишків багато — пишіть імпорт за §38.2 замість ручних документів; ручний шлях не масштабується і легко дає неправильні FIFO-дати.
