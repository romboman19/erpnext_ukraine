# Gate 0g — спільний аллокатор: evidence, 2026-07-27

## Результат

`PASS`. Ревізія ставила питання «спочатку довести, що спільний аллокатор
неможливий». Доказ протилежний: чинний `allocate_global_fifo` обслуговує
GSF-скоуп **без жодної зміни правила відбору**. Другий аллокатор не потрібен.

## Що саме перевірено

Код спайка: [`erpnext_ua/group_stock_fifo/spikes/shared_allocator.py`](../../../erpnext_ua/group_stock_fifo/spikes/shared_allocator.py).
Тести: [`erpnext_ua/group_stock_fifo/tests/test_shared_allocator_spike.py`](../../../erpnext_ua/group_stock_fifo/tests/test_shared_allocator_spike.py).
Спайк Frappe-незалежний і виконується в static-джобі CI, а не на сайті.

| # | Твердження | Тест | Статус |
|---|---|---|---|
| 1 | глобальний FIFO перетинає межі Company, продавець не має пріоритету (§4) | `test_global_fifo_crosses_companies_and_ignores_the_seller` | `PASS` |
| 2 | зріз чужого власника позначається під `MANAGEMENT_REALLOCATION` | `test_slices_owned_by_other_companies_are_flagged_for_reallocation` | `PASS` |
| 3 | особа продавця не змінює вибір шарів і собівартість | `test_seller_identity_does_not_change_the_selection` | `PASS` |
| 4 | CC-адаптер інертний поза своїми складами (0i на рівні сервісу) | `test_commission_adapter_stays_inert_outside_its_warehouses` | `PASS` |
| 5 | негативний контроль: хибний binding зводить домени докупи | `test_a_wrong_warehouse_binding_would_cross_the_domains` | `PASS` |
| 6 | детермінізм при однакових timestamp (0f fallback) | `test_identical_timestamps_stay_deterministic` | `PASS` |

## Ключове спостереження

`allocate_global_fifo` не приймає `company` взагалі. Його скоуп —
`item_code + location + allowed_warehouses`:

```python
def allocate_global_fifo(candidates, *, item_code, location, qty, allowed_warehouses, ...)
```

Різниця скоупів, яку базове ТЗ вважало причиною писати другий аллокатор
(`group + physical_location + item` проти `item + company + cc_location`),
цілком лежить у **адаптері кандидатів**, а не в правилі розподілу:

- `location` → фізична локація групи;
- `allowed_warehouses` → набір технічних складів усіх ФОП групи з
  `GSF Warehouse Binding`;
- `CandidateQuery.company` → **продавець**, а не фільтр залишку.

`preview_from_adapters` уже приймає `Sequence[CandidateAdapter]` і зводить їх в
один детермінований прогін. GSF підключається як другий адаптер.

## Сценарій §37.1

Три Company, один item, одна фізична локація, шість одиниць запиту.

| Шар | Власник | FIFO timestamp | Кількість | Собівартість одиниці |
|---|---|---|---:|---:|
| `GSF-A` | FOP A | 2026-07-27 08:00 | 2 | 1000 |
| `GSF-B` | FOP B | 2026-07-27 09:00 | 3 | 1100 |
| `GSF-C` | FOP C | 2026-07-27 10:00 | 1 | 1200 |

Продавець `FOP C`:

| # | Шар | Власник | Кількість | Вартість | Перепризначення |
|---:|---|---|---:|---:|---|
| 1 | `GSF-A` | FOP A | 2 | 2000 | так |
| 2 | `GSF-B` | FOP B | 3 | 3300 | так |
| 3 | `GSF-C` | FOP C | 1 | 1200 | ні |
| | | | **6** | **6500** | 5 одиниць |

Продавець `FOP A` дає той самий набір шарів і ту саму суму `6500`, змінюється
лише розмітка перепризначення. Це і є заборона seller-first FIFO у дії.

Собівартості одиниць (1000/1100/1200) реконструйовані під суму `6500`, яку
задає ревізія для гейта 0j — базового ТЗ v1.0 у репо немає, тож звірити з
першоджерелом неможливо. Після появи ТЗ цифри треба звірити.

## Що це **не** доводить

Спайк покриває вибір шарів, а не проведення. Поза покриттям лишається вся
бухгалтерська половина гейта 0j і гейти 0a–0e: реальні SLE/GL, точне
перенесення вартості через Material Issue/Receipt, rollback в одній транзакції,
Inventory Dimension на обох ногах. Це вимагає сайту й виконується в
`frappe-integration.yml`.

Гейт 0f закритий тут **лише на рівні аллокатора**: логічний порядок GSF
детермінований незалежно від того, як ERPNext упорядковує SLE з однаковим
posting datetime. Питання, чи збігається фізичний вибір ERPNext з логічним
вибором GSF, лишається відкритим — саме тому §16 вимагає брати вартість із
фактичного SLE.

## Наслідки для реалізації

1. **Другий аллокатор не пишеться.** GSF отримує `GSF Layer` адаптер поруч із
   `candidates_from_cc_stock_lot`. Рішення фіксується ADR-002.
2. **Одна адитивна зміна у спільному модулі.** `StockCandidate.__post_init__`
   валідує `source_method` за комісійною мапою
   `SOURCE_METHOD_RELATIONSHIP_MODEL`. Спайк тимчасово позичає `BUYOUT`/`OWN`.
   Production потребує запису `GSF_LAYER → OWN`. Більше нічого в
   `allocation.py` міняти не треба.
3. **`CandidateQuery.company` змінює зміст.** Для CC це фільтр залишку, для GSF
   — продавець. Поле треба або перейменувати, або задокументувати обидва
   значення; мовчазна двозначність тут дорога.
4. **Ізоляція доменів тримається виключно на `GSF Warehouse Binding`.** Тест 5
   показує: варто помилково додати комісійний склад до `allowed_warehouses` —
   і аллокатор чесно віддасть комісійний лот у GSF-план. Реєстр binding
   потребує власного інваріанта й тесту, а не лише документації.
5. **Планувальник має падати контрольовано.** Зараз чужий лот дає голий
   `KeyError`. Production потребує доменної помилки з кодом.

## Відтворення

```bash
python -m pytest -q erpnext_ua/group_stock_fifo/tests
```

Джоба `static-and-package` у [`ci.yml`](../../../.github/workflows/ci.yml) вже
виконує цей шлях.
