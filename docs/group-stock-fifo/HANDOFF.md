# GSF (Group Stock FIFO) — передача агенту, стан на 2026-07-30

Цей документ існує для того, щоб інший агент (або ви за тиждень) міг увійти в
роботу без повторного читання 30+ комітів історії. Він описує: що таке GSF і
навіщо, де що лежить, що зроблено і доведено, що зроблено але не доведено, що
не зроблено взагалі, і з чого продовжувати. Пишіть сюди самі, якщо після
прочитання лишається питання, на яке довелося шукати відповідь деінде — це
ознака, що документ застарів.

---

## 1. Що таке GSF і навіщо він

**Бізнес-задача.** Один фізичний магазин (мережа HUNTER.rv, м. Рівне), товар
фізично спільний, але з податкових причин продажі й закупівлі проводяться через
кілька ФОП — кожен ФОП це окрема ERPNext `Company`. Потрібно: закуповувати
товар будь-яким ФОПом, зберігати його фізично разом, а продавати — тим ФОПом,
якому вигідно з податкової точки зору саме зараз (ліміт групи ЄП, наявність
ПДВ тощо), і при цьому:

- зберігати повний і точний складський та бухгалтерський облік у кожній Company
  окремо (жодного паралельного облікового ядра);
- вести **один глобальний FIFO** по фізичному товару незалежно від того, яка
  Company його купила;
- якщо продає ФОП B, а найстаріший товар належить ФОП A — автоматично
  «перепризначити» цей товар ФОПу B без податкової фікції купівлі-продажу між
  ФОП, без внутрішньої маржі, без впливу на P&L;
- касир нічого цього не бачить і не обирає вручну.

**Технічна назва:** `Group Stock FIFO` (GSF), модуль всередині вже існуючого
застосунку `erpnext_ua` (НЕ окремий Frappe app — це свідоме рішення,
консолідація, див. розділ 3).

**Другий діючий домен на тому ж сайті:** `Consignment and Commission` (CC) —
уже реалізований і випущений модуль комісійної/консигнаційної торгівлі в тому
самому `erpnext_ua`. GSF має співіснувати з ним, не заважати йому і не
використовувати його склади. Скрізь у документах ви побачите слово "CC" — це
завжди про цей сусідній модуль.

---

## 2. Де що лежить (карта репозиторію)

### 2.1. Два фізичні місця коду — і чому

| Копія | Шлях | Роль |
|---|---|---|
| Робоча (ця) | `/root/my-claude-project/erpnext_ukraine` | тут я пишу код і документи |
| Хостова | `/home/romboman19/erpnext_ua` | bind-mount у Docker-контейнер тестового сайту |

Тестовий сайт (`postest.local`, стек `frappe-test`, `docker compose`)
**виконує код з хостової копії**, не з робочої. Тому після кожної зміни:

```bash
# у робочій копії
git add -A && git commit -m "..." && git push origin feat/gsf-phase-0

# синхронізувати хостову копію
cd /home/romboman19/erpnext_ua && git pull --ff-only
```

Обидві копії — це один і той самий git-репозиторій
(`github.com/romboman19/erpnext_ukraine`), просто два робочих дерева на одну
гілку. Немає жодного окремого репозиторію для CC чи для GSF — усе злито в один
`erpnext_ukraine` за прямим рішенням власника (див. розділ 3).

### 2.2. Гілка

Уся робота — на гілці **`feat/gsf-phase-0`**, запушеній на GitHub. Гілка ще НЕ
змержена в `main`. Це свідомо: робота не закінчена, і `main` мають чіпати
тільки завершені, приймані шматки.

### 2.3. Тестовий сайт

```
Стек:     frappe-test (docker compose), HTTP на 192.168.10.11:8081
Сайт:     postest.local
Версії:   Frappe 16.25.0, ERPNext 16.26.2, erpnext_ua 0.9.0
Apps:     erpnext, erpnext_ua, flow, frappe, print_designer
ВАЖЛИВО:  немає scheduler-контейнера — фонові задачі не виконуються
          (це і плюс, і мінус: гейти детерміновані, але Repost Item
          Valuation, наприклад, ніколи не запуститься сам)
```

Керування (доступ через `docker exec`):

```bash
docker exec frappe-test-backend-1 bench --site postest.local <команда>
docker exec -i frappe-test-backend-1 bench --site postest.local mariadb <<'SQL' ... SQL
docker exec -i frappe-test-backend-1 bench --site postest.local console <<'PY' ... PY
```

**Гейти виконуються прогонами** через `bench execute` з обов'язковим
подвійним підтвердженням (`confirm_site` + власний `confirm_write`-токен на
кожен гейт) — це запобіжник, щоб такий скрипт не запустився випадково на
production. Дивись `erpnext_ua/group_stock_fifo/spikes/*.py`.

### 2.4. Документація — усе під `docs/group-stock-fifo/`

```
docs/group-stock-fifo/
├── HANDOFF.md                    ← цей файл
├── README.md                     ← ревізія базового ТЗ під "один застосунок"
├── spec-v1.0.md                  ← БАЗОВЕ ТЗ, 2422 рядки, §0–§47, дослівно
├── spec-reconciliation.md        ← звід розбіжностей ТЗ vs ревізія vs докази
├── adr/                          ← 12 прийнятих ADR (0001–0014, без 0011)
├── spikes/
│   ├── phase-0.md                ← протокол Phase 0, зведена таблиця гейтів
│   └── evidence/                 ← один .md на кожен гейт із фактичними даними
└── release/
    ├── phase-1.md                ← звіт по Phase 1 проти §41/§43
    ├── phase-2.md                ← звіт по Phase 2, з таблицями живих прогонів
    └── phase-3.md                ← звіт по Phase 3, включно з §37.7 гонкою
```

**Якщо читаєте тільки один файл — читайте `spec-v1.0.md`.** Це ЄДИНЕ джерело
істини щодо моделі даних, інваріантів і плану фаз. Все інше — коментарі й
докази поверх нього.

**Другий за пріоритетом — `spikes/phase-0.md`.** Там зведена таблиця: який
гейт що довів, з якою evidence, і посилання на ADR, яке з цього випливло.

### 2.5. Код — усе під `erpnext_ua/group_stock_fifo/`

```
erpnext_ua/group_stock_fifo/
├── __init__.py
├── api.py                        ← whitelisted API (поки одна ручка: readiness)
├── receipts.py                   ← doc_event-хуки §11 і §17.3 (Phase 2)
├── doctype/                      ← 13 production DocType
│   ├── gsf_settings/             ┐
│   ├── gsf_company_group/        │
│   ├── gsf_group_member/         │ Phase 1 (child table)
│   ├── gsf_physical_location/    │
│   ├── gsf_location_company_binding/
│   ├── gsf_warehouse_binding/    │
│   ├── gsf_staging_lane/         ┘
│   ├── gsf_stock_layer/          ┐
│   ├── gsf_layer_balance/        │ Phase 2
│   ├── gsf_layer_movement/       ┘
│   ├── gsf_allocation/           ┐
│   ├── gsf_allocation_slice/     │ Phase 3 (slice — child table)
│   └── gsf_scope_lock/           ┘
├── services/
│   ├── domain.py                 ← ЧИСТІ функції-правила, без Frappe (§28.3)
│   ├── readiness.py              ← §30 readiness, використовує domain.py
│   ├── layers.py                 ← write-path реєстру шарів (Phase 2)
│   ├── reservation.py            ← ЧИСТІ правила §13.4/§9.12 (Phase 3)
│   ├── candidates.py             ← §12.2 адаптер кандидатів (Phase 3)
│   └── allocations.py            ← write-path резервування, §13.1–§13.3
├── setup/
│   ├── roles.py                  ← ідемпотентний provisioning 6 ролей
│   └── layer_dimension.py        ← вимір + патч ADR-002 + індекси
├── tests/                         ← юніт-тести домену, без сайту
│   ├── test_foundation_domain.py  ← 27 тестів
│   ├── test_layer_domain.py       ← 32 тести (Phase 2)
│   ├── test_reservation_domain.py ← 25 тестів (Phase 3)
│   └── test_shared_allocator_spike.py
├── integration_tests/             ← прогони НА САЙТІ, з confirm-токенами
│   ├── phase_3_fixture.py         ← build/teardown фікстури §37.1
│   ├── phase_3_checks.py          ← функціональний прогін Phase 3
│   └── phase_3_race.py            ← ОДИН racer; запускати ДВА процеси (§37.7)
└── spikes/                        ← КОД ГЕЙТІВ. Це не production, це докази.
    ├── fixtures.py                 (3 ФОП HUNTER.rv — будівник тестових даних)
    ├── dimension.py, stock_setup.py, preflight.py, shared_allocator.py (утиліти)
    └── gate_0b.py … gate_0k.py     (виконувані докази, див. розділ 4)
```

**Критична відмінність:** `spikes/` — це не заготовки production-коду, це
одноразові виконувані докази з жорсткими guard-ами (`ALLOWED_SITES`,
`confirm_write`-токени), які не мають потрапити в production-шлях. Коли
писатимете Phase 2+, дещо з логіки `spikes/dimension.py` і `spikes/preflight.py`
доведеться **переписати načisto** як production-сервіс — не імпортувати спайк
напряму.

---

## 3. Ключове архітектурне рішення, яке треба знати одразу

Базове ТЗ (`spec-v1.0.md`) написане під **окремий Frappe app**
`erpnext_group_stock_fifo`. Це рішення **скасоване**. GSF — це модуль
всередині вже існуючого `erpnext_ua`, поруч із CC. Причина: власник прямо
попросив злити все в один репозиторій `erpnext_ukraine`, і ревізія (README.md)
це задокументувала ще до того, як спливло само ТЗ.

Наслідок для читання ТЗ: скрізь, де в `spec-v1.0.md` написано
`erpnext_group_stock_fifo/...`, читайте `erpnext_ua/group_stock_fifo/...`.
Скрізь, де написано "окремий hooks.py" — це один спільний `erpnext_ua/hooks.py`.

---

## 4. Що ЗРОБЛЕНО і ДОВЕДЕНО (Phase 0 — спайки)

Phase 0 за §41 ТЗ — це прототипи, які мають довести найризикованіші
припущення ДО написання production DocType-моделі. Вона закрита вердиктом:

> **`GO WITH CONSTRAINTS`**, з 6 явними обмеженнями, зафіксованими в ADR.

Дев'ять гейтів пройдено `PASS` (0a, 0b, 0c, 0d, 0e, 0f, 0g, 0j, 0k). Два
частково (0h — схема так, форми не перевірялись; 0i — сервісний рівень так,
хуки ще не існують — нема що перевіряти).

### Таблиця гейтів і що кожен реально довів

| Гейт | Питання | Результат | Наслідок |
|---|---|---|---|
| **0a** | Material Issue на clearing-рахунок не чіпає P&L | `PASS` (закрито разом з 0b) | — |
| **0b** | Вартість source issue = вартості destination receipt, точно | `PASS`, навіть при "забрудненому" цільовому складі і при дробовому rate (1000/3) | ADR-003 |
| **0c** | Продаж зі Sale Stage списує рівно підготовлену вартість | `PASS` для чистого складу, **FAIL-виявлення** для забрудненого: 2500 замість 2000 | **Найважливіший гейт.** Довів: Inventory Dimension НЕ керує тим, який шар спишеться — вона лише мітка постфактум. FIFO черга ERPNext працює на рівні `item+warehouse`, ігноруючи мітку шару в рядку продажу |
| **0d** | Вимір шару доїжджає до SLE на обох ногах перепризначення + продажу | `PASS` | Знахідка: вимір на `Stock Entry Detail` — це ДВА поля (`gsf_x` для вихідної ноги, `to_gsf_x` для вхідної), а не одне |
| **0e** | Rollback усього ланцюжка (issue+receipt+sale) в одній транзакції | `PASS`, savepoint rollback знімає SLE+GL+документи разом | Знахідка: імена документів ERPNext НЕ стабільні (revert_series_if_last повертає лічильник назад при видаленні) → ключі ідемпотентності не можуть спиратись на імена |
| **0f** | Порядок ERPNext при однаковому posting datetime | `PASS`: детермінований, консьюмиться шар, **поданий першим** | Наслідок: документи перепризначення МАЮТЬ подаватись у порядку, який вирішив аллокатор — порядок подачі і є tie-breaker |
| **0g** | Чи потрібен GSF власний FIFO-аллокатор, окремий від CC | `PASS`: НІ. `allocate_global_fifo` вже company-agnostic, GSF — просто другий адаптер кандидатів | ADR-013 |
| **0j** | Наскрізний сценарій §37.1: 3 Company, COGS=6500 | `PASS`, точний збіг з тестовим сценарієм ТЗ | Підтвердив увесь ланцюжок разом |
| **0k** | Як передбачити, що спише ERPNext, ДО фактичного списання (§17 preflight) | `PASS`: читати `Stock Ledger Entry.stock_queue` (JSON) і прогнати через `erpnext.stock.valuation.FIFOValuation` — той самий клас, яким ERPNext сам користується. Жодного запису, жодного savepoint | **Закрив ADR-007**, найбільшу прогалину. Побічна знахідка: один рядок Material Issue НЕ може охопити два шари — вимір відхиляє за негативним залишком, тобто §14.4/§18.2 (окремий рядок на shar) — вимога платформи, не рекомендація |

### Три найважливіші висновки з усієї Phase 0 (якщо запам'ятати тільки це)

1. **Inventory Dimension — це аудит-мітка, не механізм оцінки.** Собівартість
   завжди читається з фактичного `Stock Ledger Entry` після проведення
   документа, ніколи з власного реєстру шарів GSF. Це не стиль — інакше буде
   розбіжність з бухгалтерією.

2. **Sale Stage має вміщувати РІВНО один чек одночасно.** Не "один склад на
   компанію", не "один склад на касу" — рівно на один активний checkout. Тому
   ADR-006 вимагає пул іменованих `GSF Staging Lane` з lock-механізмом, а не
   динамічне створення складу на кожен чек.

3. **Preflight перед кожним issue — не опція.** Перед тим, як списати товар з
   пулу конкретного ФОПа, треба прочитати `stock_queue` цього складу і
   передбачити, що спише ERPNext, порівняти з планом аллокатора, і заблокувати
   операцію при розбіжності. Механізм готовий і доведений (гейт 0k,
   `spikes/preflight.py`), але ще НЕ є частиною production-коду.

---

## 5. ADR — усі прийняті рішення (14 штук, без 0011)

Нумерація підігнана під §40 базового ТЗ (це саме по собі було окремою роботою —
довелось переносити файли і виправляти всі перехресні посилання).

| № | Назва | Статус | Суть в одному реченні |
|---|---|---|---|
| [0001](adr/0001-stock-domain-ownership.md) | Stock-domain ownership | Accepted | `GSF Warehouse Binding` — єдиний реєстр, хто володіє складом; один склад = один домен |
| [0002](adr/0002-inventory-dimension-coexistence.md) | Inventory Dimension coexistence | Accepted | Вимір не керує оцінкою (гейт 0c); власний `after_migrate`-патч прибирає GSF-поле з чужих (CC) DocType після реєстрації |
| [0003](adr/0003-exact-value-intercompany-reallocation.md) | Exact-value reallocation | Accepted | Вартість перенесення читається з `stock_value_difference` факту, ніколи не рахується наперед |
| [0004](adr/0004-posting-order.md) | Posting order | Accepted | Документи подаються в порядку, який вирішив аллокатор (не потрібні штучні зсуви часу) |
| [0005](adr/0005-balance-sheet-clearing-accounting.md) | Balance-sheet clearing | Accepted | Два рахунки на компанію (`Due From`/`Due To`) + dimension `Counterparty Accounting Company`, накопичена позиція — очікувана, елімінується на груповій звітності |
| [0006](adr/0006-stage-lane-isolation.md) | Stage lane isolation | Accepted | Пул `GSF Staging Lane` з lock/zero-check, НЕ склад-на-чек (це рішення ЗМІНИЛОСЬ під час роботи — раніше було "склад на чек", тепер пул lanes за §9.8 ТЗ) |
| [0007](adr/0007-valuation-queue-preflight.md) | Valuation queue preflight | Accepted (гейт 0k) | `FIFOValuation` replay на `stock_queue`, без запису |
| [0008](adr/0008-transaction-boundary.md) | Transaction boundary | Accepted | Rollback через savepoint (гейт 0e), а не ручна компенсація до commit |
| [0009](adr/0009-return-fifo-policy.md) | Return FIFO policy | Accepted | Повернення = новий шар з датою повернення, компанія повернення = компанія продажу |
| [0010](adr/0010-backdated-and-revaluation-policy.md) | Backdated/revaluation | Accepted | MVP блокує заднім числом, повний revaluation engine — поза MVP |
| — | CC compatibility contract | Скасовано ревізією | Нема сенсу — один застосунок, немає версійного контракту між репо |
| [0012](adr/0012-pos-prro-saga.md) | POS/PRRO saga | Accepted | `GSF Checkout` — це МАРШРУТ під існуючим `POS Order`, не окрема сага з власним payment/fiscal state |
| [0013](adr/0013-one-allocator-two-adapters.md) | Один аллокатор, два адаптери | Accepted | GSF не пише свій FIFO-аллокатор — використовує спільний з CC |
| [0014](adr/0014-idempotency-and-stable-keys.md) | Idempotency and stable keys | Accepted | Ключі ідемпотентності — власні (не імена ERPNext-документів) |

---

## 6. Що ЗРОБЛЕНО, але це ще НЕ повна логіка (Phase 1 — foundation)

Phase 1 за §41 закрита в обсязі "foundation", НЕ більше. Детальний звіт —
`docs/group-stock-fifo/release/phase-1.md`. Коротко:

**Готово:**
- 7 production DocType (список у розділі 2.5), усі змігровані на
  `postest.local` і перевірені.
- `GSF Settings.enabled` за замовчуванням `0`, і контролер **відмовляється**
  його увімкнути, поки `readiness()` повертає хоч один blocking check —
  перевірено живцем на сайті.
- `services/domain.py` — 180 рядків чистих функцій (без `frappe.*`
  імпортів) з правилами: ексклюзивність warehouse binding, валідація групи
  (одна валюта, без дублів компаній), lock-перевірка lane (zero-balance
  перед locking), `ReadinessReport`.
- `services/readiness.py` — складає §30.2 blocking checks + §30.3 warnings з
  живих таблиць.
- `setup/roles.py` — створює 6 ролей ідемпотентно, викликається і з
  `after_install`, і з `after_migrate` (після CC-хука, порядок навмисний, це
  ADR-001).
- 27 юніт-тестів на `domain.py`, усі проходять.
- **Знайдений і виправлений живий баг:** перевірка "чужий домен уже займає
  склад" не спрацьовувала через `autoname = field:warehouse` — новий рядок для
  вже зайнятого складу приходить з іменем **наявного** рядка, і фільтр
  `name != self.name` ховав саме той конфлікт, який мав ловити. Юніт-тести
  цього не бачили (вони працюють з чистими даними, не знають про autoname).
  Знайдено тільки прогоном на живому сайті. **Урок: контролери DocType
  обов'язково перевіряти на сайті, не лише юніт-тестами домену.**

**Свідомо НЕ зроблено в Phase 1 (і чому):**
- **Warehouse provisioning** (сервіс, що САМ створює технічні склади за §7.5) —
  відкладено, бо без CC discovery він міг би створити склад поверх уже
  зайнятого CC-складом.
- **CC discovery** (§8.3, автовиявлення `CC Location` і реєстрація їх як
  `DISCOVERED_EXTERNAL`) — відкладено, бо на тестовому сайті зараз **немає
  жодного `CC Location`**, реалізація без цього була б без доказу.
- **Workspace/UI** — не є передумовою Phase 2.

---

## 6a. Що ЗРОБЛЕНО в Phase 2 — layer registry

Детальний звіт із таблицями живих прогонів —
`docs/group-stock-fifo/release/phase-2.md`. Коротко:

**Готово:**
- `GSF Stock Layer` (§9.9), `GSF Layer Balance` (§9.10), `GSF Layer Movement`
  (§9.11).
- Production Inventory Dimension `GSF Stock Layer` + патч ADR-002, який
  прибирає GSF-поля з DocType цього застосунку і **перевіряє власний
  результат** — валить міграцію, якщо щось вціліло. На сайті: 22 поля на ядрі
  ERPNext, 0 на `erpnext_ua`, комісійні поля не зачеплені.
- Хуки §11 на `Purchase Receipt`, `Purchase Invoice` (`update_stock = 1`) і
  керований `Stock Entry` Material Receipt: `PENDING`-шар до submit, `OPEN` +
  `ORIGIN_RECEIPT` + баланс після submit, зі значеннями з фактичного SLE.
- §11.4 скасування: guard (шар, який уже рухався, не дає скасувати) +
  `REVERSAL` + `CANCELLED`.
- §17.3: unmanaged `Stock Entry` / `Stock Reconciliation` у GSF-пул
  відхиляється (`UNCLASSIFIED_GSF_STOCK`). Поза GSF-складами хуки інертні —
  перевірено окремим прогоном.
- 32 юніт-тести на нові правила `domain.py`.

**Три речі, які варто знати про цей код:**
1. **Ім'я шару — це його ідентичність** (§11.3, хеш координат у `GSFL-…`).
   Перевірка існування І Є перевіркою ідемпотентності; окремого реєстру ключів
   немає і не потрібно.
2. **Розділення `before_submit` / `on_submit` вимушене:** шар має існувати до
   submit, щоб вимір доїхав у книгу (гейт 0d), а кількість і вартість читаються
   тільки після submit з фактичного SLE (гейт 0c, ADR-003).
3. **Патч ADR-002 стрижений за модулями застосунку, не за списком імен**, щоб
   новий комісійний DocType зі складським полем не повернув забруднення тихо.

**НЕ зроблено в Phase 2:**
- `GSF Opening Stock Import` (§38.2) — потрібен лише на реальному запуску.
- Integrity report (§31.6) — кеш балансу вже пишеться так, щоб розбіжність було
  видно (`integrity_status`), але сам звіт і його scheduled job без
  scheduler-контейнера не перевірити.
- **Живий прогон на Batch/Serial товарі.** Код читає `Serial and Batch Bundle`
  і відмовляє, якщо один рядок несе кілька партій, але на сайті немає
  трекінгового товару. Це перше, що варто закрити фікстурою.

---

## 6b. Що ЗРОБЛЕНО в Phase 3 — global FIFO reservation

Детальний звіт — `docs/group-stock-fifo/release/phase-3.md`. Коротко:

**Готово:** адаптер кандидатів §12.2 поверх спільного аллокатора,
`GSF Allocation` / `GSF Allocation Slice` / `GSF Scope Lock`, порядок локів
§13.2, TTL, ідемпотентність §13.4, чотири ручки API. У спільному коді змінено
рівно один рядок — `GSF_LAYER -> OWN`, саме той, що передбачив гейт 0g.

**Три речі, які варто знати про цей код:**
1. **Кількість — з книги, резерв — з рядка балансу.** Несиметрично навмисно:
   `actual_qty_cache` має право відставати (§9.10), тож доступне береться
   агрегатом SLE; резерв у книзі не представлений взагалі, тож ним володіє
   `reserved_qty_cache`, і кожен інкремент — умовний `UPDATE`, захищений щойно
   прочитаним балансом книги під row lock.
2. **Порядок кандидатів з адаптера — частина контракту.** Спільний аллокатор
   сортує за ключем, який є ПРЕФІКСОМ §12.3; сортування стабільне, тож адаптер
   повертає рядки вже відсортованими за повним ключем, і саме це дає хвостовий
   tie-break за company/warehouse. Зламати можна тихо.
3. **Serial-шари відхиляються (`SERIAL_AMBIGUOUS`), а не апроксимуються** —
   поштучної правди з книги адаптер не читає.

**Доведено живцем (§37.1 і §37.7):** продавець, який володіє 5 із 10 одиниць,
все одно отримує зрізи 2+3+1 від найстарішого власника; сума 6 500 збіглася з
ТЗ. Два процеси на двох з'єднаннях, які одночасно беруть увесь пул: рівно один
виграє, книга 10 — резерв 10, переможець чергується між прогонами.

---

## 7. Що НЕ ЗРОБЛЕНО ВЗАГАЛІ — залишок за §41

Це найважливіший розділ для того, хто продовжує. Порядок — як у §41 ТЗ.

### Phase 2 — залишок (не блокує Phase 3)
- `GSF Opening Stock Import` (§38.2).
- Integrity report (§31.6) — потребує стека зі scheduler.
- Фікстура трекінгового товару + прогін приймання на Batch і на Serial.

### Phase 3 — залишок (не блокує Phase 4)
- **Serial allocation** — наразі fail-closed (`SERIAL_AMBIGUOUS`). Потрібна
  поштучна правда з книги про те, які серійники шару ще на складі, і фікстура
  трекінгового товару.
- `expire_due_allocations()` написана, але **свідомо не підключена** до
  `scheduler_events` — див. §8, пастка №7.
- Item policy (§12.2) — DocType політики товару в моделі §9 немає; запит несе
  `item_policy` і зберігає снапшот, правил поверх нього ще нема.

### Phase 4 — Stock reallocation (НАСТУПНИЙ КРОК)
- Реалізація самого перепризначення: source Material Issue → читання
  фактичного SLE → destination Material Receipt на цю вартість (техніка
  доведена в гейтах 0b/0k, коду виробничого рівня нема).
- **Тут же виробничий preflight** — переписати `spikes/preflight.py` в
  `services/preflight.py`, підключити до Material Issue hook.
- Same-company transfer (§14.2) окремо від cross-company (§14.3) — гейт 0j
  показав, що спайк наразі веде ОБИДВА через clearing-рахунок для простоти;
  production має розділити (same-company = звичайний Material Transfer, без
  clearing).

### Phase 5 — Managed sale
- Sales Invoice builder з множинними рядками (один на шар/зріз, §18.2) —
  ТЕХНІЧНО ДОВЕДЕНО, що рядок не може охопити два шари (гейт 0k), сам builder
  не написаний.
- `GSF Checkout` як маршрут під `POS Order` (ADR-012) — саги, стани §23.1,
  компенсація.

### Phase 6 — POS/PRRO
- Інтеграція з фіскалізацією. `POS Order` вже існує в `erpnext_ua` (не GSF), і
  ADR-012 каже, що фіскалізацію ІНІЦІЮЄ `POS Order`, GSF туди не лізе.

### Phase 7 — Returns and inventory count
- ADR-009 написаний, коду немає: новий шар при поверненні, quarantine для
  Serial/Batch.
- `GSF Physical Stock Count` (§20.3).

### Phase 8 — Hardening
- Load tests, deadlock tests, failure injection на РЕАЛЬНОМУ scheduler-стеку
  (поточний тестовий стек `frappe-test` **не має scheduler-контейнера** —
  вибухне як тільки з'явиться будь-яка scheduled job).
- 17 runbooks за §45 — **жодного не написано**.
- Production acceptance — це операція власника, агент її не виконує.

### Взагалі не почато, не в жодній фазі явно
- Concurrency-тест на реальне double-booking (§37.7) — CC-модуль має схожий
  тест, GSF ще ні.
- CC compatibility suite (§37.24–37.29) — жодного інтеграційного тесту
  GSF+CC разом.
- Financial Integrity report (§31.6, §37.23).

---

## 8. Вісім пасток, у які я вже вступив — щоб ви не вступали повторно

1. **`bench migrate` при першому підключенні нового модуля треба ДВІЧІ.**
   Перший прохід створює Module Def, другий синкає DocType. Якщо після
   першого migrate DocType не з'явились — це не помилка, просто мігруйте ще
   раз.

2. **`autoname = field:X` ламає наївні "виключити поточний рядок" перевірки.**
   Новий (ще не збережений) документ з `autoname` на дублікатне значення
   отримує ІМ'Я НАЯВНОГО рядка ще до insert. Фільтр `name != self.name` в
   такому разі виключає саме той конфліктний рядок, який мали знайти.
   Правильно: перевіряти `if not self.is_new(): filters["name"] = ("!=", self.name)`.

3. **Inventory Dimension на `Stock Entry Detail` — це ДВА поля**, не одне:
   `gsf_x` для вихідної ноги (`s_warehouse`), `to_gsf_x` для вхідної
   (`t_warehouse`). Переплутаєте — вимір мовчки не потрапить у книгу, і впаде
   НАСТУПНИЙ (не цей) документ з незрозумілою помилкою про негативний
   залишок.

4. **`apply_to_all_doctypes=1` засіває поле на ВСІ DocType, що торкаються
   складу — включно з чужим доменом (CC).** Явного способу обмежити список
   кількох конкретних DocType в ERPNext НЕМАЄ (`document_type` — одиничний
   Link, перевірено експериментально). Рішення (ADR-002): лишити
   `apply_to_all_doctypes=1`, а власним `after_migrate`-патчем видаляти
   custom fields з чужих DocType одразу після реєстрації.

5. **Скасовані рядки Stock Ledger Entry переживають видалення батьківського
   документа** (лишаються з `is_cancelled=1`), а `delete_doc` найновішого
   документа ще й ВІДКОЧУЄ naming series — тому наступний прогін отримує ТІ Ж
   САМІ імена документів, і перевірка "чи вижив цей ваучер" за іменем дає
   хибний результат. Порівнюйте за МНОЖИНОЮ живих рядків, не за іменами
   (див. `gate_0e.py`).

6. **`Purchase Receipt` на цьому сайті не проводиться «просто так».** Хук
   `erpnext_ua.ua_receiving.service.validate_purchase_receipt` вимагає
   українські реквізити первинного документа: `supplier_delivery_note`,
   `ua_supplier_document_type` (Select із фіксованими значеннями, напр.
   `Видаткова накладна постачальника`), `ua_supplier_document_date`,
   `ua_received_by` (Link на `User`), `ua_receipt_verified` і, якщо
   `Buying Settings.ua_require_supplier_document_attachment` увімкнено,
   `ua_supplier_document_file`. Будь-яка тестова фікстура з приходом має їх
   заповнювати, інакше падає ще до GSF-хуків.

7. **Резерв позиції — ОДНЕ число, спільне для всіх allocation, які її
   тримають.** Тому будь-яка операція, що зменшує `reserved_qty_cache`, мусить
   бути справді one-shot: повторний виклик віддає в пул одиниці, які ще тримає
   інша allocation. `validate_allocation_transition` дозволяє статусу лишитися
   на місці (це потрібно контролеру), тож захист має бути в сервісі — саме на
   цьому Phase 3 і спіймали живий баг. Правило: перехід у термінальний статус
   перевіряти на «вже там», перш ніж виконувати побічний ефект.

8. **Не підключайте scheduled job, поки немає стека зі scheduler.**
   `frappe-test` не має scheduler-контейнера, тож задача, зареєстрована в
   `scheduler_events`, ніколи не виконається в тесті й одразу виконається в
   проді. `expire_due_allocations()` навмисно лишена як звичайна функція; на
   стеку `frappe-uat` scheduler є — саме там її і треба вмикати (Phase 8).

---

## 9. Як перевіряти будь-яку зміну (мінімальний ритуал)

```bash
# у робочій копії, після кожної зміни:
cd /root/my-claude-project/erpnext_ukraine
python3 -m ruff check
python3 -m compileall -q erpnext_ua
python3 -m pytest -q erpnext_ua/consignment_and_commission/tests erpnext_ua/group_stock_fifo/tests
python3 tools/check_hooks.py
find erpnext_ua -name '*.json' -print0 | xargs -0 -n1 jq -e . >/dev/null && echo "JSON OK"

# коміт + push
git add -A && git commit -m "..." && git push origin feat/gsf-phase-0

# синхронізація хостової копії (сайт бачить ТІЛЬКИ її)
cd /home/romboman19/erpnext_ua && git pull --ff-only

# якщо змінювались DocType/hooks — мігрувати (ДВІЧІ якщо новий модуль)
docker exec frappe-test-backend-1 bench --site postest.local migrate
```

Для гейтів (`spikes/gate_*.py`) і для прогонів на сайті
(`integration_tests/phase_*.py`) — команда прогону написана в докстрінгу
кожного файлу. Прогони Phase 3 **комітять** дані, тому teardown обов'язковий:

```bash
docker exec frappe-test-backend-1 bench --site postest.local execute \
  erpnext_ua.group_stock_fifo.integration_tests.phase_3_fixture.teardown \
  --kwargs '{"confirm_write": "DROP_GSF_PHASE_3"}'
```

---

## 10. Рекомендований наступний крок

**Почати Phase 4 (stock reallocation)**, у такому порядку:

1. **Виробничий preflight** — переписати `spikes/preflight.py` начисто в
   `services/preflight.py`. Робити ЦЕ першим, бо все інше в Phase 4 має право
   писати в книгу тільки після того, як preflight сказав «те, що спише ERPNext,
   збігається з планом». Техніка доведена гейтом 0k, коду немає.
2. Same-company transfer (§14.2) окремо від cross-company (§14.3). Гейт 0j вів
   ОБИДВА через clearing-рахунок для простоти спайка; production має розділити:
   same-company — звичайний Material Transfer, без clearing.
3. Cross-company: source Material Issue → прочитати фактичний SLE → destination
   Material Receipt рівно на цю вартість (гейти 0b/0k). Вартість НЕ рахувати
   наперед — ADR-003.
4. Stage lane: `GSF Staging Lane` — це рівень 1 порядку §13.2, і Phase 4 перша,
   хто його реально бере. Lock-порядок уже реалізований у
   `services/allocations.py`; беріть його звідти, а не пишіть заново.

Вхідні дані для Phase 4 вже є: `GSF Allocation Slice.requires_reallocation`
позначає саме ті зрізи, які належать не продавцю, і `source_balance_key` вказує
на рядок балансу, який треба зменшити.

Перед тим, як писати warehouse provisioning і CC discovery (відкладені з
Phase 1) — треба фікстуру з реальним `CC Location` на тестовому сайті, інакше
discovery буде написаний без доказу.

Найдешевше, що можна закрити принагідно: фікстура трекінгового товару. Вона
закриває одразу дві дірки — приймання на Batch/Serial (Phase 2) і serial
allocation (Phase 3), яка зараз чесно fail-closed.

---

## 11. Одна річ, яку я НЕ можу зробити замість вас

§43 Definition of Done закінчується "production acceptance виконаний на
staging-копії реальних даних". Це операція власника бізнесу — потрібні
реальні дані, реальне рішення про запуск, реальна відповідальність за
наслідки. Я довожу логіку гейтами і тестами; прийняття в продакшн — ваше
рішення, не моє.
