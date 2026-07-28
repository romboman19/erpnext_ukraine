# Phase 1 — Foundation: стан

Дата: 2026-07-28. Обсяг за [§41](../spec-v1.0.md) Phase 1.

## Зроблено

| Пункт §41 | Стан |
| --- | --- |
| app skeleton | ✅ модуль `Group Stock FIFO` у `erpnext_ua`, за ревізією §1 |
| settings | ✅ `GSF Settings` (§9.2), feature gate `enabled = 0` |
| roles | ✅ шість ролей §32, ідемпотентний provisioning |
| Company Group | ✅ `GSF Company Group` + `GSF Group Member` (§9.3, §9.4) |
| Physical Location | ✅ `GSF Physical Location` (§9.5) |
| bindings | ✅ `GSF Location Company Binding` (§6.3), `GSF Warehouse Binding` (§8) |
| readiness | ✅ §30.2 blocking + §30.3 warnings, `GET /diagnostics/readiness` |
| feature gate off | ✅ і **не відкривається**, поки readiness блокує |
| warehouse provisioning | ⬜ склади створюються вручну; сервіс не написаний |
| CC discovery | ⬜ §8.3 автовиявлення `CC Location` не написане |

Понад §41 зроблено `GSF Staging Lane` (§9.8) — його вимагає
[ADR-006](../adr/0006-stage-lane-isolation.md), і без нього Phase 4 не почати.

## Перевірено на `postest.local`

- `bench migrate` створює всі сім DocType. **Потрібні два проходи:** перший
  створює Module Def, другий синкає DocType. Це відома поведінка Frappe при
  першому підключенні модуля, і вона має потрапити в runbook `installation.md`.
- readiness повертає `blocked` з єдиною причиною «No enabled GSF Company Group
  exists» — правильно для порожнього сайту.
- спроба увімкнути `GSF Settings.enabled` при блокуючому readiness **відхилена**.
- чотири guard-и `GSF Warehouse Binding` спрацьовують: чужий домен, груповий
  склад, невідповідність компанії, невідома роль.

## Знайдене живим тестом

Перевірка «склад уже належить іншому домену» **не спрацьовувала**. `autoname` —
`field:warehouse`, тому новий рядок для вже прив'язаного складу приходить із
**іменем наявного рядка**, і фільтр `name != self.name` відсікав саме той рядок,
який шукали. Insert падав пізніше на unique-індексі — fail-closed, але з
`DuplicateEntryError` замість `CC_WAREHOUSE_CONFLICT`.

Юніт-тести цього не бачили: вони працюють з чистими функціями, які про
`autoname` не знають. Це аргумент за те, щоб guard-и перевірялися ще й на сайті,
а не лише в домені.

## Не зроблено і чому

- **warehouse provisioning** — сервіс, який створює технічні склади за §7.5.
  Свідомо відкладено: спершу треба CC discovery, інакше provisioning може
  створити склад поверх комісійного.
- **CC discovery (§8.3)** — на тестовому сайті нема жодного `CC Location`, тож
  реалізація була б без доказу. Потрібна фікстура комісійної локації.
- **workspace** — UI не є передумовою Phase 2.

## Definition of Done (§43): що вже можна відмітити

| Критерій | Стан |
| --- | --- |
| Усі ADR прийняті | 12 із 13 за §40; 011 скасований ревізією |
| Feature gate за замовчуванням off | ✅ і захищений readiness |
| Clean install та repeated migrate | ⚠️ migrate перевірено на наявному сайті, clean-site CI ще не ганявся |
| N Company доведено тестом ≥3 Company | ✅ Phase 0, гейт 0j |
| Global FIFO без seller-first | ✅ гейт 0g, 0j |
| Source value = destination value | ✅ гейт 0b |
| Sale COGS = prepared stage value | ✅ гейт 0c |
| Internal reallocation P&L = 0 | ✅ гейт 0b |
| Concurrency без overselling | ⬜ не перевірялося |
| Idempotency без дублікатів | ⬜ не перевірялося |
| Return workflow | ⬜ ADR є, коду немає |
| Backdated fail closed | ⬜ ADR є, коду немає |
| CC compatibility suite | ⬜ |
| GSF/CC overlap неможливий | ✅ guard працює, перевірено на сайті |
| Readiness без blocking checks | ⬜ очікувано: групи ще нема |
| Financial integrity report | ⬜ |
| Runbooks | ⬜ жодного з 17 (§45) |
| Production acceptance | ⬜ операція власника, не моя |
