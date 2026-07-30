# Phase 2 — Layer registry: стан

Дата: 2026-07-30. Обсяг за [§41](../spec-v1.0.md) Phase 2.

## Зроблено

| Пункт §41 | Стан |
| --- | --- |
| Inventory Dimension | ✅ `GSF Stock Layer`, `apply_to_all_doctypes = 1` + патч прибирання чужих полів за [ADR-002](../adr/0002-inventory-dimension-coexistence.md) |
| Stock Layer | ✅ `GSF Stock Layer` (§9.9) з детермінованим ID за §11.3 |
| Layer Balance | ✅ `GSF Layer Balance` (§9.10), ключ = ім'я документа |
| Layer Movement | ✅ `GSF Layer Movement` (§9.11), immutable у контролері |
| receipt hooks | ✅ Purchase Receipt, Purchase Invoice (`update_stock`), керований Stock Entry Material Receipt (§11.1–§11.4) |
| §17.3 мінімізація | ✅ unmanaged Stock Entry / Stock Reconciliation у GSF-пул відхиляється |
| opening import | ⬜ `GSF Opening Stock Import` (§38.2) — до реального запуску |
| integrity report | ⬜ §31.6 не написаний |

Понад §41: §11.4 скасування надходження (guard + reversal + `CANCELLED`), бо без
нього перший же скасований прихід лишав би шар, який ні на що не спирається.

## Ключові рішення всередині фази

**Ім'я шару — це його ідентичність.** §11.3 перелічує координати, з яких
збирається детермінований ID; ми хешуємо їх у `GSFL-<32 hex>` і робимо ім'ям
документа. Наслідок: повторна обробка того самого рядка не створює другий шар, а
натикається на наявний — перевірка існування **і є** перевіркою ідемпотентності,
окремий реєстр ключів не потрібен.

Єдина координата, яка порушує [ADR-014](../adr/0014-idempotency-and-stable-keys.md),
— `origin_document`, ім'я ERPNext-документа, яке гейт 0e показав нестабільним.
Це допустимо саме тут і більше ніде: шар, чийого origin-документа вже немає, не
має існувати сам (§11.4), тож перевикористане ім'я може зіткнутися лише з шаром,
який мав зникнути разом із документом.

**Патч ADR-002 стрижений за модулями, не за списком імен.** Денилист — «усі
DocType цього застосунку», а не «`CC Stock Lot`, `CC Allocation`, …»: новий
комісійний DocType зі складським полем інакше повернув би забруднення, якого
патч не знає. Після прибирання патч **перевіряє власний результат** і валить
міграцію, якщо поле десь вціліло.

**Робота розділена між `before_submit` і `on_submit` не з естетики.** Шар має
існувати до submit, щоб його вимір доїхав у книгу (гейт 0d); кількість, вартість
і FIFO-дата читаються тільки після submit, з фактичного SLE (гейт 0c,
[ADR-003](../adr/0003-exact-value-intercompany-reallocation.md)). Нічого не
рахується наперед і не приймається на віру.

## Перевірено на `postest.local`

`bench migrate` — один прохід (модуль `Group Stock FIFO` існує з Phase 1, пастка
подвійного migrate стосується лише першого підключення модуля).

Схема:

- три DocType змігровані;
- вимір `GSF Stock Layer` створений; поле є на `Stock Entry Detail` (обидва:
  `gsf_stock_layer` і `to_gsf_stock_layer`), `Stock Ledger Entry`,
  `Purchase Receipt Item`, `Purchase Invoice Item`, `Sales Invoice Item`;
- 22 custom field на ядрі ERPNext, **0 на DocType цього застосунку** — патч
  ADR-002 відпрацював; 31 поле `cc_stock_lot` не зачеплене;
- індекси `gsf_layer_balance` (SLE) і `gsf_layer_fifo` (шари) створені.

Контролери (прогін у транзакції з rollback, після нього 0 рядків):

| Спроба | Результат |
| --- | --- |
| повторний insert того самого рядка приходу | `DuplicateEntryError` на детермінованому імені |
| інший `origin_row_name` | окремий шар |
| `PENDING → EXHAUSTED` | відхилено |
| `CANCELLED → OPEN` | відхилено |
| зміна `item_code` у стані `OPEN` | відхилено, у повідомленні названо поле |
| зміна `blocked_reason` у стані `OPEN` | дозволено |
| SERIAL-шар з 1 серійником на 10 одиниць | `SERIAL_AMBIGUOUS` |
| редагування руху | відхилено |
| видалення руху | відхилено |
| дубль `idempotency_key` | `UniqueValidationError` |
| невідомий `movement_type` | відхилено |
| `is_reversal` без `reversal_of` | відхилено |
| друга позиція на той самий layer/company/warehouse | `DuplicateEntryError` |
| `reserved > actual` у балансі | `NEGATIVE_STOCK_RISK` |

Наскрізний §11 (два прогони, обидва з rollback):

| Джерело | Що доведено |
| --- | --- |
| керований `Stock Entry` Material Receipt, 4 × 250 | шар `OPEN`, qty 4, FIFO-дата = posting datetime документа (**не** `now()`), тег у `to_gsf_stock_layer` (вихідне поле порожнє), SLE несе `gsf_stock_layer`, рух `ORIGIN_RECEIPT` зі `stock_value` 1000 = `stock_value_difference` книги, баланс 4/1000 |
| `Purchase Receipt`, 5 × 120 | те саме через одинарне поле `gsf_stock_layer`; FIFO-дата `2026-07-22 10:30:00`, рух і баланс 600 = 5 × 120 з книги |
| unmanaged `Stock Entry` у GSF-пул | відхилено, `UNCLASSIFIED_GSF_STOCK` |
| unmanaged `Stock Entry` у звичайний склад | **проведено без перешкод** — хуки інертні поза доменом |
| скасування приходу | рух `REVERSAL` −4, баланс 0/0, шар `CANCELLED` |

Обидва прогони відкривали feature gate живцем: readiness пройшов на фікстурі з
однією Company, групою та біндингами.

## Не зроблено і чому

- **`GSF Opening Stock Import` (§38.2)** — потрібен лише на реальному запуску, і
  його форма залежить від того, як власник вивантажить наявні залишки.
- **Integrity report (§31.6)** — має порівнювати кеш `GSF Layer Balance` з
  агрегатом SLE. Кеш уже пишеться так, щоб розбіжність було видно
  (`integrity_status`), але сам звіт і scheduled job без scheduler-контейнера на
  `frappe-test` не перевірити — це робота Phase 8 на стеку зі scheduler.
- **Serial/Batch через `Serial and Batch Bundle`** — код читає bundle і
  відмовляє (`BATCH_MISMATCH`), якщо один рядок несе кілька партій, але живого
  прогону на трекінговому товарі не було: на сайті немає ні batch-, ні
  serial-товару. Це перше, що треба зафіксувати фікстурою.

## Відома шорсткість

Повторний insert шару з десктопа падає `DuplicateEntryError`, а не §33-кодом.
Для сервісного шляху це правильно — `ensure_pending_layer` спершу шукає наявний
шар і повертає його, — але руками створений дубль отримує платформену помилку.
Виправляти варто тоді, коли (і якщо) шари взагалі стануть створюваними руками.
