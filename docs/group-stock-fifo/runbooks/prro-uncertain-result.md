# Runbook: невизначений результат ПРРО

Фіскалізацію ініціює `POS Order`, не GSF (ADR-012). GSF лише читає результат.

## Симптом

Checkout у `FISCAL_PENDING` або `MANUAL_REVIEW` з `FISCALIZATION_UNCERTAIN`.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> execute frappe.client.get_list --kwargs '{"doctype":"GSF Checkout","filters":{"status":["in",["FISCAL_PENDING","FISCAL_RETRY","MANUAL_REVIEW"]]},"fields":["name","fiscal_state","sales_invoice"]}'
```

## Чого НЕ робити

Не скасовувати Sales Invoice, доки не відомо, чи є фіскальний чек. Подвійний чек дорожчий за відсутній.

## Виправлення

З'ясуйте стан у ПРРО-домені. Далі виставте `fiscal_state` на checkout (`DONE` або `FAILED`) і викличте `checkout_resume` — сага доведе справу до кінця сама.

## Перевірка

Checkout у `COMPLETED`, `completed_at` заповнений.

## Rollback / ескалація

Запас уже списаний продажем — компенсація тут не застосовується. Якщо чек не відбувся і не відбудеться, це повернення (§19.4).
