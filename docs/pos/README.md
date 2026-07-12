# UA POS — архітектура та реалізація

Модуль POS для українського роздрібу: мультиФОП, управлінська каса, власний ПРРО (API ДПС).
Цільова платформа: **Frappe 16.25 / ERPNext 16.26** (прод erp.huntervua.pp.ua, docker compose, кастомний образ `erpnext-huntervua`).

## Зміст пакета

| Файл | Зміст |
|---|---|
| [01-gap-analysis.md](01-gap-analysis.md) | Що дає стандартний ERPNext v16, що беремо, що не покриває |
| [02-architecture.md](02-architecture.md) | Архітектура, межа core/custom, аналіз існуючих репо, схема інтеграцій |
| [03-data-model.md](03-data-model.md) | Стандартні документи, custom DocType, ER-діаграма, поля/статуси/індекси |
| [04-state-machines.md](04-state-machines.md) | State diagrams: продаж, платіж, фіскалізація, управлінська зміна, повернення |
| [05-api-contracts.md](05-api-contracts.md) | Fiscal adapter, terminal adapter, hardware agent, POS REST API |
| [06-ui-wireframes.md](06-ui-wireframes.md) | Wireframes екранів, гарячі клавіші, UX-правила |
| [07-permissions.md](07-permissions.md) | Ролі та матриця дозволів |
| [08-plan-risks-questions.md](08-plan-risks-questions.md) | План по етапах, ризики, blocking questions, міграція/оновлення |
| [09-testing.md](09-testing.md) | Стратегія тестування + 36 acceptance-сценаріїв |
| [10-stage0-spike-report.md](10-stage0-spike-report.md) | **Етап 0 виконано**: тестовий стек + ПРРО e2e, критичні знахідки (TSP, REST/gRPC, деплой) |
| [11-cashier-guide.md](11-cashier-guide.md) | Коротка інструкція касира для готового core POS |

Діаграми — у форматі Mermaid (рендеряться на GitHub / у VS Code з розширенням Mermaid).

## Ключові архітектурні рішення (TL;DR)

1. **POS живе в `erpnext_ua`** як новий модуль `ua_pos` (поряд з `ua_fop`, `ua_fiscal`).
   Банківські касові термінали також належать `ua_pos`, бо є частиною checkout-saga.
   SMS, банківські виписки, онлайн-еквайринг і доставка лишаються в `ukrainian_integrations`.
   Ядро ERPNext не змінюється: тільки DocType, custom fields (fixtures), hooks, whitelisted API, власна сторінка.
2. **Обліковий документ продажу — стандартний Sales Invoice** (`is_pos=1`, `update_stock=1`),
   створюється сервером. Стандартні POS Invoice / point-of-sale UI / POS Opening/Closing Entry **не використовуються**
   (обґрунтування в 01-gap-analysis).
3. **Оркестрація продажу — custom DocType `POS Order`** (saga-корінь): кошик, маршрутизація ФОП,
   спліт на кілька Sales Invoice, платіжні спроби, фіскальні чеки, друк, стани відновлення, lookup-токен чека.
4. **МультиФОП, фаза 1 — одна управлінська Company + ФОП як вимір** (custom field + Accounting Dimension).
   Юридично значущий облік доходу ФОП ведеться виключно Z-звітами ПРРО, а не GL.
   Перехід на «Company на кожен ФОП» — окрема фаза зі спроєктованою міграцією.
   **Це blocking question №1** (див. 08).
5. **Управлінська зміна — custom** (`POS Operational Shift` + журнал `POS Cash Movement`),
   незалежна від фіскальних змін; одна управлінська зміна ↔ N `PRRO Shift` (по ФОП × ПРРО).
6. **Фіскалізація — існуючий `ua_fiscal`** (fiscal_client → API ДПС + prro-signer), обгорнутий
   абстрактним `FiscalAdapter`; `PRRO Receipt` розширюється до повноцінного журналу фіскальних операцій з ідемпотентністю.
7. **Термінал — `TerminalAdapter` у `ua_pos`** поверх pb-pos-gateway. Адаптер передає
   `operation_id`, не повторює `sale` після timeout і виходить з `unknown` лише через `/status`.
8. **Друк чеків — server-side** на мережеві ESC/POS принтери (TCP 9100) через чергу `POS Print Job`;
   локальний hardware-agent — запасний варіант, якщо принтери виявляться USB-only (blocking question).
9. **UI — власна повноекранна сторінка `/pos`** (Vue 3, бандл в апці, esbuild), desktop-first,
   barcode-first, без перезавантажень, статуси через socket.io.

## Blocking questions — ЗАКРИТО (інтерв'ю з власником, 2026-07-11/12)

Усі 15 питань + 5 уточнень вирішені — деталі в [08](08-plan-risks-questions.md §3). Ключові рішення:
модель ФОП = одна Company + вимір; дохід ФОП рахується **виключно з Z-звітів ПРРО**; валюта
приймається, але рахується як гривня за курсом дня (решта — завжди грн); принтери всі мережеві
(агент не потрібен); «кредит» = банк-розстрочка з ручною відміткою (власний workflow вилучено);
сертифікати не фіскалізуються (фаза 7). Відкритих залежностей для старту немає.

## Поточний стан

- Core POS працює наскрізно: вхід касира, відкриття/закриття зміни з покупюрним
  перерахунком, складський пошук, кошик, знижка, відкладені чеки, готівкова оплата,
  решта, Sales Invoice, повне/часткове повернення, касові операції, друк товарного
  чека та звіти зміни.
- TerminalAdapter підтримує sale/refund/status і відновлення `Payment Unknown` без
  повторного списання; живий прогін потребує налаштованого PB POS Terminal.
- FiscalAdapter відкриває/закриває зміну ПРРО та фіскалізує продаж/повернення;
  живий прогін потребує тестового/робочого ПРРО і КЕП.
- Ідентифікація клієнта підключена окремим контрактом із
  `erpnext_ukraine_integrations` (SMS, Telegram, контрольний дзвінок).
- До production gate лишаються апаратні прогони термінала/ПРРО/мережевого принтера,
  мульти-ФОП routing, offline recovery та повний набір acceptance-сценаріїв на пілоті.
