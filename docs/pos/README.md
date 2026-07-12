# UA POS — проєктна документація (етап 1: архітектура, без коду)

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

Діаграми — у форматі Mermaid (рендеряться на GitHub / у VS Code з розширенням Mermaid).

## Ключові архітектурні рішення (TL;DR)

1. **POS живе в `erpnext_ua`** як новий модуль `ua_pos` (поряд з `ua_fop`, `ua_fiscal`).
   Термінали/SMS/банки лишаються в `ukrainian_integrations` і викликаються через адаптери.
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
7. **Термінал — `TerminalAdapter`** поверх існуючого privat_pos (pb-pos-gateway). Обов'язкова доробка
   gateway: запит статусу за `operation_id` (зараз відсутній — без нього не закрити сценарій `unknown`).
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

**Наступний крок — етап 0**: тестовий site + e2e-спайк фіскалізації на тестовому API ДПС
(перевірка CAdES-T/TSP — головний технічний ризик). Код продукту не пишемо, доки спайк не зніме
невизначеність по протоколу.
