# Gate 0d — вимір шару в складській книзі: evidence, 2026-07-27

## Результат

`PASS` за всіма шістьма перевірками. Вимір шару доїжджає до Stock Ledger Entry
на всіх трьох документах ланцюжка, і друга Inventory Dimension співіснує з
комісійною.

Разом з цим закрито половину **0h**.

## Носій виміру

`GSF Spike Layer` — DocType з `custom = 1`, поля `item_code`, `owner_company`,
`source_layer`. Ім'я навмисно не збігається з майбутнім продакшн-DocType, щоб
файловий `GSF Stock Layer` не зіткнувся з залишком у базі.

Inventory Dimension: `reference_document = GSF Spike Layer`,
`apply_to_all_doctypes = 1`, `validate_negative_stock = 1` — дзеркально до
комісійної.

Код: [`gate_0d.py`](../../../erpnext_ua/group_stock_fifo/spikes/gate_0d.py).

```bash
bench --site postest.local execute erpnext_ua.group_stock_fifo.spikes.gate_0d.run \
  --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0D"}'
```

## Ланцюжок

Шари: `GSF-LAYER-00001` (власник ФКРВ) і `GSF-LAYER-00002`
(власник ФКІВ, `source_layer = GSF-LAYER-00001`).

| Документ | Склад | Кількість | Вартість | `gsf_spike_layer` у SLE |
|---|---|---:|---:|---|
| `MAT-STE-2026-00001` seed | Пул - ФКРВ | +2 | +2000.00 | `GSF-LAYER-00001` |
| `MAT-STE-2026-00002` issue | Пул - ФКРВ | −2 | −2000.00 | `GSF-LAYER-00001` |
| `MAT-STE-2026-00003` receipt | Комплектування - ФКІВ | +2 | +2000.00 | `GSF-LAYER-00002` |
| `ACC-SINV-2026-00001` продаж | Комплектування - ФКІВ | −2 | −2000.00 | `GSF-LAYER-00002` |

Продаж у таблиці — бонус понад питання гейта: він доводить, що вимір переживає
і Sales Invoice з `update_stock=1`, тобто шлях §16 працює до самого списання
собівартості.

| Перевірка | Стан |
|---|---|
| `sle_column_created` | ✅ |
| `both_dimensions_coexist` | ✅ `CC Stock Lot` + `GSF Spike Layer` |
| `cc_column_untouched` | ✅ |
| `issue_carries_source_layer` | ✅ |
| `receipt_carries_target_layer` | ✅ |
| `sale_carries_target_layer` | ✅ |

## Знахідка 1: вимір — це два поля, а не одне

На `Stock Entry Detail` Inventory Dimension створює **пару** полів:

| Поле | Нога |
|---|---|
| `gsf_spike_layer` | вихідна (`s_warehouse`) |
| `to_gsf_spike_layer` | вхідна (`t_warehouse`) |

Перша редакція гейта клала шар у просте поле на Material Receipt. Вимір не
потрапив у книгу мовчки, а впав уже наступний Material Issue — з
`InventoryDimensionNegativeStockError`: «2 одиниці потрібні на складі з виміром
`gsf_spike_layer: GSF-LAYER-00001`», бо з таким виміром на складі не лежало
нічого.

Помилка корисна тим, що вона **fail-closed**: неправильно проставлений вимір не
дає тихо розійтися залишкам, а зупиняє наступну операцію. Але діагностика
оманлива — падає не той документ, що помилився.

На `Sales Invoice Item` і `Delivery Note Item` пара така сама; на
`Purchase Receipt Item` і `Purchase Invoice Item` полів три —
додається `rejected_*` і `from_*`.

## Знахідка 2: виміри взаємно засівають доменні DocType

`apply_to_all_doctypes = 1` створює поле на всьому, що торкається складу —
включно з чужим доменом:

| Вимір | Усього custom fields | З них на DocType чужого домену |
|---|---:|---:|
| `CC Stock Lot` | 38 | 0 на `GSF Spike Layer` |
| `GSF Spike Layer` | 36 | **8 на `CC *`** |

Асиметрія не випадкова: комісійний вимір створювався тоді, коли GSF-DocType ще
не існував. Тобто **результат залежить від порядку створення**, і після
наступного `bench migrate` картина може змінитися.

Ревізія ТЗ каже, що 0h спрощується, «бо обидві dimension створюються з одного
`after_migrate` і їх порядок контрольований». Гейт показує, чому це не просто
зручність, а вимога: без фіксованого порядку дві установки одного застосунку
отримають різну схему. До цього додається пряме порушення правила володіння
даними — GSF-поле фізично з'являється на `CC Stock Lot`, `CC Allocation`,
`CC Receipt Item` і ще п'яти комісійних таблицях.

Вирішено в [ADR-002](../../adr/0002-inventory-dimension-coexistence.md):
явного переліку кількох DocType платформа не підтримує (`document_type` — одиничний
Link, перевірено окремим пробним запуском). `apply_to_all_doctypes` лишається, а
власний `after_migrate`-патч прибирає GSF-поле з комісійних DocType одразу після
того, як ERPNext їх зареєструвала.

## Стан схеми після гейта

Обидві dimension активні, обидві колонки в `Stock Ledger Entry` присутні,
конфліктів на формах не виявлено. Прибрати все разом з custom fields:

```bash
bench --site postest.local execute erpnext_ua.group_stock_fifo.spikes.gate_0d.cleanup \
  --kwargs '{"confirm_site":"postest.local","confirm_write":"CLEAN_GATE_0D"}'
```

`Inventory Dimension.on_trash` видаляє створені поля сам, тому схема
повертається до попереднього стану.

## Межі доказу

- Перевірено Stock Entry (Material Issue/Receipt) і Sales Invoice. Material
  Transfer, Stock Reconciliation, Delivery Note і Purchase Receipt не
  перевірялися.
- Serial/Batch не задіяні: item без відстеження. Взаємодія виміру з
  Serial and Batch Bundle — окреме питання, у комісійному домені воно свого
  часу дало `NATIVE FAIL` з контрольованим fallback.
- Друга половина 0h (форми, відсутність конфлікту в UI) візуально не
  перевірялася — тільки схема й книга.
