# Privat POS Flow (ERPNext custom app)

Цей документ описує фактичний контур інтеграції POS-терміналів ПриватБанку в кастомній апці `ukrainian_integrations`.

## 1. Архітектура

```text
ERPNext (ukrainian_integrations)
  -> HTTP (gateway_url + api_key)
pb-pos-gateway (Go service)
  -> TCP :2000
POS terminal (Ingenico/PAX/...)
```

- ERP викликає whitelisted методи в `ukrainian_integrations.payments.privat_pos.service`.
- Gateway endpoint/ключ беруться з `PB POS Settings` (fallback: `site_config`).
- Gateway спілкується з терміналом по TCP (`ip_address`, `tcp_port`, зазвичай 2000).

## 2. Де в коді

- UI-кнопки форми терміналу:
  - `ukrainian_integrations/public/js/pb_pos_terminal_actions.js`
- Hooks підключення JS до DocType:
  - `ukrainian_integrations/hooks.py` (`doctype_js["PB POS Terminal"]`)
- Backend сервіс:
  - `ukrainian_integrations/payments/privat_pos/service.py`
- Gateway client:
  - `ukrainian_integrations/payments/privat_pos/gateway_client.py`

## 3. Кнопки в DocType `PB POS Terminal`

1. `🟢 Тест зв'язку`
2. `💳 Тест оплати`
3. `↩️ Тест повернення`

Кнопки працюють у формі **конкретного збереженого документа** (не list view).

## 4. Публічні методи (ERP)

- `pb_pos_test_connection(terminal)`
- `pb_pos_test_payment(terminal, amount)`
- `pb_pos_test_refund(terminal, amount, reference_operation_id=None)`
- `pb_pos_sale(sales_invoice, terminal_ip, amount=None, terminal_port=2000)`

## 5. Логіка резолву налаштувань

1. `PB POS Settings.gateway_url`
2. `PB POS Settings.api_key` (password field)
3. fallback на `site_config`:
   - `pb_pos_gateway_url`
   - `pb_pos_api_key`

## 6. Логіка резолву терміналу

З DocType `PB POS Terminal`:
- `ip_address`
- `tcp_port`
- `is_active`

Пошук працює по `name` або `terminal_name`.

## 7. Legacy-поведінка gateway (поточний production-контур)

Gateway у вашому контурі працює в legacy-режимі:
- verify/check: `/verify`
- sale: `/purchase`
- refund: `/refund` (`invoiceNumber` для reference)

Тому в ERP-клієнті реалізовано fallback/сумісність із legacy шляхами.

## 8. Типові помилки та інтерпретація

- `таймаут відправки запиту до горутини` — проблема worker/reconnect на gateway (транспортний шар)
- `connection refused` / `EOF` — мережа або TCP-сесія до терміналу
- `Cannot obtain receipt: log file is empty (0001)` на verify — часто бізнес-стан терміналу, не обов'язково мережевий фейл
- `methodNotImplemented` — метод не підтримується моделлю/прошивкою

## 9. Мінімальний чек після деплою

1. Відкрити `PB POS Terminal` документ і перевірити наявність 3 кнопок.
2. `🟢 Тест зв'язку` на кожному терміналі.
3. `💳 Тест оплати` на тестовій сумі.
4. Перевірити записи в `Hunter Integration Log`.

## 10. Операційні нотатки

- Для стабільності gateway має бути в тій самій мережі/VLAN, що термінали.
- Не запускати зайву паралель на один і той самий термінал.
- Після таймаутів фіноперацій робити reconcile (перевірку останнього статусу) перед повторною оплатою.
