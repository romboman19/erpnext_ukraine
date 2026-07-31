# Runbook: закриття періоду

Закриття фіксує, що минуле більше не змінюється. Тому воно й перевіряє, що змінювати вже нічого.

## Симптом

`close_period` відмовляє зі списком причин.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_integrity
```

## Чого НЕ робити

Не закривати період із CRITICAL-знахідками — `close_period` відмовить, і обхід зробить розбіжність постійною.

## Виправлення

Кожен блокер називає себе: відкриті allocation → `stuck-allocation.md`; незавершені reallocation → `manual-review.md`; чеки в польоті → доведіть або скасуйте; CRITICAL — за відповідним runbook.

## Перевірка

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.services.period.close_period --kwargs '{"closed_through":"YYYY-MM-DD"}'
```

## Rollback / ескалація

Закрити період назад не можна: це тихо відкрило б уже закрите. Якщо треба переглянути закритий період — це рішення власника, не операція.
