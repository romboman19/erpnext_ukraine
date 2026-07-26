# ERPNext Consignment and Commission

Встановлюваний Frappe/ERPNext застосунок для викупу, відстроченої закупівлі,
комісійної та консигнаційної торгівлі.

> Статус: release `1.1.0`, готовий до контрольованого staging/production
> rollout на Frappe/ERPNext v16. CI перевіряє Python 3.14, чисте встановлення,
> повторну міграцію, FIFO, П(С)БО-проводки, рахунок 024, повернення та
> multi-currency розрахунки.

Застосунок не змінює ядро Frappe/ERPNext. Feature gate після встановлення
вимкнений, тому встановлення й міграція не починають проводити операції без
явного налаштування та активації.

## Що реалізовано

- чотири методи приймання: `BUYOUT`, `DEFERRED_PURCHASE`, `COMMISSION` і
  `CONSIGNMENT`;
- стандартний stock-updating Purchase Invoice, Stock Asset і Supplier payable
  для власного товару;
- zero-value Stock Entry для фізичного руху стороннього товару без хибного
  активу компанії та окремий простий позабалансовий облік на рахунку 024;
- immutable `CC Stock Lot` як джерело, власник і точний вимір залишку;
- один глобальний FIFO для всіх чотирьох методів без пріоритету методу оплати;
- атомарні резервування з TTL, idempotency, row locks та exact Serial/Batch;
- керований Sales Invoice, незмінні sale-allocation snapshots і контрольоване
  каскадне скасування;
- versioned API для продажу, повернення, settlement, платежу, POS checkout,
  повернення партнеру та conversion third-party → OWN;
- persistent split POS saga, payment state, print queue, retry, compensation і
  manual-review state;
- Settlement Report, часткові оплати, строк боргу, валютний курс на дату
  платежу та стандартні ERPNext Exchange Gain/Loss документи;
- повернення покупця до і після settlement з точним сторнуванням кількості,
  доходу й партнерського зобов'язання;
- контрольоване повернення непроданого товару партнеру;
- контрольована купівля залишку партнера з точним Serial/Batch mapping;
- ролі manager/user/auditor, Company permissions, Workspace та операційні
  звіти `CC FIFO Inventory`, `CC Sale Financials`, `CC Partner Balance`,
  `CC POS Queue` і `CC Financial Integrity`;
- scheduler jobs для прострочених резервів і черги друку;
- read-only readiness та financial-integrity API.

## FIFO й П(С)БО-фінансова модель

FIFO порівнює фактичний час надходження всіх доступних `CC Stock Lot` одного
Item/Company/Location. `BUYOUT`, `DEFERRED_PURCHASE`, `COMMISSION` і
`CONSIGNMENT` не утворюють окремих черг і не мають прихованого пріоритету.
Sales Invoice отримує точні allocation slices та `CC Stock Lot` Inventory
Dimension; для Serial/Batch додатково фіксується фізична identity.

Товар, право власності на який залишається у комітента/консигнанта, не стає
запасом компанії. Під час приймання він збільшує рахунок 024 за вартістю акта,
а продаж, повернення партнеру або conversion у `OWN` зменшує 024. Повернення
покупця відновлює той самий позабалансовий залишок. Аналітика ведеться за
партнером, товаром, складом і Serial/Batch; кожен рух має посилання на документ
та ідемпотентний ключ. Вартість 024 завжди вводиться й зберігається у валюті
Company; валюта договору окремо застосовується до продажу, боргу та платежів.

Фінанси зберігаються immutable snapshot на кожному проданому slice:

- `OWN`: дохід і COGS проводить стандартний ERPNext; борг Supplier виникає з
  Purchase Invoice під час приймання/конвертації;
- `COMMISSION`: Sales Invoice відображає отриману від покупця суму через 702;
  recognition JE дебетує 704 на суму комітента, переносить винагороду з 702 на
  703 та кредитує зобов'язання перед комітентом на 685;
- `CONSIGNMENT` без переходу права власності: партнерська сума визначається як
  quantity × зафіксована partner unit rate, але П(С)БО-подання таке саме —
  доходом компанії залишається лише винагорода на 703, без 902/COGS;
- валовий товарний дохід і COGS дозволені лише після явного conversion
  third-party → `OWN`, який списує 024 і створює стандартний Purchase Invoice;
- від'ємна маржа fail-closed: ціну продажу або партнерську ставку потрібно
  виправити до проведення документа;
- суми документа й базової валюти зберігаються окремо, а округлення завжди
  зберігає рівність `gross = retained + partner obligation`.

Для нетрекінгового власного товару ERPNext оцінює COGS за стандартною
warehouse-level valuation queue; `CC Stock Lot` є точною чергою кількості та
аудиту, а не паралельним valuation ledger. Якщо потрібна собівартість кожної
фізичної одиниці, Item має використовувати Serial/Batch valuation.

Окремі FIFO lots одного Item проводяться окремими рядками стандартних Purchase
і Sales Invoice, тому перед активацією потрібно ввімкнути `Allow Item to be
Added Multiple Times in a Transaction` у Buying Settings і Selling Settings.
`CC Settings` перевіряє це fail-closed і не дозволяє активувати модуль із
несумісною глобальною конфігурацією.

Для кожної валюти керованого продажу потрібен enabled Selling Price List у цій
самій валюті. Builder вибирає Customer default, Selling Settings default або
перший детермінований active list; якщо сумісного list немає, продаж
відхиляється до створення документа. POS додатково приймає лише наявний Mode of
Payment.

ERPNext setup має бути завершений до активації модуля: стандартні Party Type
повинні містити відповідності `Customer → Receivable` та `Supplier → Payable`.
Діагностика модуля перевіряє їх fail-closed разом з іншими prerequisites.

## Залежності

Обов'язкові залежності — `erpnext` v16 та `erpnext_ua` `0.3.0+`. Остання
надає українські плани рахунків №291/№186 і простий журнал класу 0. Опціональні
адаптери:

- `ukrainian_integrations`: платежі, комунікації та інші провайдери;
- `erpnext_ukraine_prro_signer`: зовнішній підпис фіскального контуру.

Відсутність опціонального app не блокує core receipt/sale/settlement workflow.
Конкретний фіскальний або друкарський провайдер потрібно налаштувати й окремо
прийняти до rollout.

## Встановлення

Спочатку зробіть backup і rehearsal на staging. Повна процедура, конфігурація,
smoke checks та rollback: [`deployment/README.md`](deployment/README.md).

```bash
bench get-app https://github.com/romboman19/erpnext_ukraine
bench --site <site> install-app erpnext_ua
bench get-app https://github.com/romboman19/erpnext_consignment_and_commission
bench --site <site> install-app erpnext_consignment_and_commission
bench --site <site> migrate
bench --site <site> execute \
  erpnext_consignment_and_commission.consignment_and_commission.setup.psbo_accounting.setup_psbo_account_mapping \
  --kwargs '{"company":"<Company>","overwrite":1}'
bench --site <site> execute \
  erpnext_consignment_and_commission.consignment_and_commission.services.diagnostics.collect_environment
```

Успішна діагностика повертає `status: ready_for_acceptance` і порожній
`blocking_checks`. Для кожного рядка `CC Receipt` бухгалтер задає додатну
`024 Unit Value (Company Currency)` із документа приймання або його перерахунок
у функціональну валюту Company. Після цього налаштуйте `CC Location`, профілі
партнерів, договори та multiple-item settings; лише потім увімкніть
`CC Settings.enabled`.

Readiness API доступний користувачам із роллю `System Manager`:

```text
GET /api/method/erpnext_consignment_and_commission.consignment_and_commission.api.v1.diagnostics.readiness
```

## Розроблення й перевірка

```bash
python -m pip install -e . ruff pytest
ruff check .
python -m compileall -q erpnext_consignment_and_commission
pytest -q erpnext_consignment_and_commission/consignment_and_commission/tests
```

Frappe integration suite запускається на окремому clean site через workflow
`.github/workflows/frappe-integration.yml`. Фактичний acceptance релізу:
[`docs/release/1.0.0-acceptance.md`](docs/release/1.0.0-acceptance.md).

Архітектурні рішення та завершений план робіт знаходяться у [`docs/`](docs/).
