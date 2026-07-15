# Міграція Privat POS до ERPNext Ukraine

Інтеграцію фізичних банківських терміналів перенесено з `ukrainian_integrations`
до модуля `erpnext_ua.ua_pos`. Операція термінала є частиною POS checkout-saga і
мусить мати спільну ідемпотентність, журнал та recovery-процедуру з касовим продажем.

## Нові компоненти

- `erpnext_ua/ua_pos/adapters/terminal.py` — контракт `TerminalAdapter`, клієнт
  `pb-pos-gateway` і реалізація PrivatBank;
- `erpnext_ua/ua_pos/terminal_service.py` — налаштування та діагностичні API;
- `PB POS Settings`, `PB POS Terminal` — DocType у модулі UA POS;
- `POS Payment Attempt`, `Terminal Transaction` — незмінний журнал касових платежів.

## Що змінилося в протоколі

- кожен `sale/refund/void` має стабільний `operation_id`;
- після timeout каса не повторює `sale`, а виконує тільки `status(operation_id)`;
- gateway повинен ідемпотентно повертати збережений результат для повторного
  `operation_id` і підтримувати `/status` та `/void`;
- legacy `/purchase` і `/refund` отримують `operation_id` у `params`.

## Оновлення інсталяції

1. Оновити обидва застосунки.
2. Виконати `bench --site <site> migrate`.
3. Перевірити `PB POS Settings` та переприв'язати `PB POS Terminal` до `POS Cash Desk`.
4. Оновити `pb-pos-gateway` до версії з idempotency/status/void.
5. Перевірити connection test, тестову оплату та status lookup.

Python app name `erpnext_ua` не змінюється, тому перейменування GitHub-репозиторію
не потребує перевстановлення застосунку.
