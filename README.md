# ERPNext Ukrainian Integrations

Комплексна ERPNext/Frappe app для українських інтеграцій:

- **Shipment UA**: Нова Пошта, Укрпошта (TTN, адреси, контакти, статуси)
- **PBX + SMS**: VitalPBX, TurboSMS
- **Payments UA**: Monobank, PrivatBank (Autoclient API), LiqPay, Privat POS
- **Ecommerce UA**: провайдери маркетплейсів / синхронізація замовлень

---

## Підтримуваний стек

- Frappe / ERPNext: **v16**
- Python: 3.10+
- Режим деплою: Docker Bench / standard bench

---

## Поточний функціонал

### 1) Доставка (Shipment)

- створення ТТН з ERP (standalone + document flow)
- підбір/створення контактів НП
- обробка статусів відправлень
- UI-дії на формах (Sales Invoice, Sender Profiles)

### 2) VitalPBX

- ініціація дзвінків із ERP
- webhook подій дзвінків
- журнал дзвінків `VitalPBX Call Log`
- realtime popup для користувача (визначення по `User.vitalpbx_extension`):
  - номер/напрямок
  - клієнт
  - останні замовлення

**Безпека webhook:**
- endpoint захищений ключем (`key` query param або `X-Webhook-Key` / `X-VitalPBX-Key` header)
- ключ читається з:
  1) `site_config.vitalpbx_webhook_key`
  2) `VitalPBX Settings.webhook_key`

### 3) TurboSMS

- кнопка відправки в `TurboSMS Settings`
- popup-форма: **sender + phone + text**
- серверний send API
- логування в:
  - `Hunter Integration Log`
  - `TurboSMS Log` (queued/success/error)

### 4) Payments UA

#### Monobank
- імпорт statement у `Bank Transaction`
- settings DocType: `Monobank Settings`
- scheduler auto-import (з toggle в settings)

#### PrivatBank (Autoclient API)
- імпорт `transactions` / `balance`
- підтримка пагінації (`exist_next_page`, `next_page_id` → `followId`)
- settings DocType: `PrivatBank Settings`
- параметр нормалізації сум (`amount_in_minor_units`)

#### LiqPay
- ініціація платежів
- callback обробка (`server_url`)
- settings DocType: `LiqPay Settings`

> Важливо: callback LiqPay має проходити перевірку підпису `data/signature` перед змінами статусів.

---

### 5) Privat POS (касові термінали)

- інтеграція через `PB POS Settings` + `PB POS Terminal`
- тестові дії прямо у формі термінала:
  - `🟢 Тест зв'язку`
  - `💳 Тест оплати`
  - `↩️ Тест повернення`
- backend методи в `ukrainian_integrations/payments/privat_pos/service.py`
- gateway client в `ukrainian_integrations/payments/privat_pos/gateway_client.py`
- client-side кнопки в `ukrainian_integrations/public/js/pb_pos_terminal_actions.js`

Детальний технічний опис: `docs/privat_pos_flow.md`


## DocType налаштувань (створені)

- `VitalPBX Settings` (включно з `webhook_key`)
- `Monobank Settings`
- `PrivatBank Settings`
- `LiqPay Settings`
- `PB POS Settings`

---

## Базовий flow синхронізації банку

1. Користувач заповнює credentials у Settings DocType.
2. Manual Test/Import (ручний запуск) перевіряє доступ.
3. Scheduler робить інкрементальний імпорт.
4. Транзакції створюються як `Bank Transaction` з idempotency guard.
5. Логи/помилки пишуться в integration logs.

### Idempotency (рекомендовано)

- Monobank: `statement_item_id` / комбінація `time + amount + description + account`
- PrivatBank: **`REF + REFN`** (офіційна рекомендація)
- LiqPay callback: `order_id + transaction_id`

---

## Встановлення

```bash
bench get-app https://github.com/romboman19/erpnext_ukrainian_integrations.git
bench --site <site> install-app ukrainian_integrations
bench --site <site> migrate
```

Docker Bench (типовий):

1. app має бути присутній у всіх python-контейнерах (`backend`, `scheduler`, `queue-*`)
2. `pip install -e apps/ukrainian_integrations`
3. `bench migrate`
4. restart backend/scheduler/workers

---

## Швидкий чек після деплою

- `bench --site <site> list-apps`
- `bench --site <site> migrate`
- `bench doctor`
- перевірка assets:
  - `vitalpbx_popup_listener.js`
  - `turbosms_settings_actions.js`
- smoke API:
  - VitalPBX webhook (без ключа -> deny, з ключем -> ok)
  - TurboSMS test send
  - Manual bank import

---

## Відомі ризики / TODO

- завершити strict signature verify для LiqPay callback
- додати role checks для чутливих whitelisted методів
- прибрати дублювання/монолітність у payments service layer
- доопрацювати test coverage (integration + migration)

---

## Changelog (recent highlights)

- додано webhook security для VitalPBX
- додано realtime popup по дзвінках
- виправлено TTN standalone bug (`rec_name`)
- додано TurboSMS dialog + логування в `TurboSMS Log`
- додано settings DocType для Monobank / PrivatBank / LiqPay

