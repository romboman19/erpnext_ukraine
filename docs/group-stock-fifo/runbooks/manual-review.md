# Runbook: ручний розбір

`MANUAL_REVIEW` — це стан, у якому система свідомо перестала вгадувати.

## Симптом

Checkout або reallocation у `MANUAL_REVIEW`; у `failure_code` — конкретний код §33.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> execute frappe.client.get_list --kwargs '{"doctype":"GSF Checkout","filters":{"status":"MANUAL_REVIEW"},"fields":["name","failure_code","manual_review_reason","sales_invoice"]}'
```

## Чого НЕ робити

Не редагувати `GSF Allocation`, `GSF Stock Reallocation` чи `GSF Layer Movement` руками. Вони server-owned, і контролери відмовлять — це навмисно.

## Виправлення

У `GSF Checkout` вручну редагуються тільки `manual_review_reason` і `payment_state`. Далі — рішення:
- запас ще в lane, продажу не було → `checkout_abort` (компенсує або звільнить, за станом);
- продаж уже проведено → це повернення, а не скасування: `returns` (§19.4).

## Перевірка

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_integrity
```

## Rollback / ескалація

Якщо стан незрозумілий — не вгадуйте. `GSF Layer Movement` — незмінний журнал: він показує, що саме сталося і в якому порядку.
