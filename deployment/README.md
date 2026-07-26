# Production deployment runbook

Цей runbook розгортає модуль `Consignment and Commission` без автоматичної
активації операцій. До `erpnext_ua` 0.9.0 модуль постачався окремим застосунком
`erpnext_consignment_and_commission` release `1.1.0`; тепер він входить до
`erpnext_ua`, і окремий `install-app` для нього більше не потрібен.
Реліз перевіряється на Frappe/ERPNext v16, Python 3.14 і MariaDB 10.6. Перед
rollout звірте сумісність на копії site та зафіксуйте git commit.

## 1. Передумови

- чинний ERPNext v16 site без незавершеної `bench migrate`;
- український план рахунків №291 або №186, застосований модулем `UA Accounting`;
- працездатні backend, queue workers і scheduler;
- повний file/database backup та перевірене місце зберігання backup;
- окремий staging site з копією production schema/configuration;
- визначені Company, Warehouses, CC Locations, Suppliers, Customers, Items,
  currencies, Mode of Payment та бухгалтерські accounts;
- enabled Selling Price List для кожної валюти, у якій створюватимуться
  керовані Sales Invoice;
- завершений ERPNext setup зі стандартними Party Type `Customer → Receivable`
  і `Supplier → Payable`;
- `Allow Item to be Added Multiple Times in a Transaction` увімкнено у Buying
  Settings і Selling Settings, щоб окремі ownership lots одного Item не
  втрачали свою Inventory Dimension identity;
- бухгалтер погодив mapping 024/702/703/704/685, джерело вартості акта
  приймання та формули commission/consignment;
- для Serial/Batch Item активовано стандартний ERPNext tracking;
- для POS/фіскального друку прийнято конкретний provider adapter.

Не активуйте feature gate під час міграції. Не запускайте test-site bootstrap
або `spikes/*` на production.

## 2. Backup і preflight

```bash
bench --site <site> backup --with-files
bench --site <site> list-apps
bench --site <site> doctor
```

Збережіть поточний apps commit/branch і перевірте, що backup читається. Для
оновлення наявної інсталяції спочатку вимкніть `CC Settings.enabled` і дочекайтеся
завершення активних checkout/print jobs або переведіть їх у manual review.

## 3. Staging install/upgrade

Нове встановлення:

```bash
bench get-app https://github.com/romboman19/erpnext_ukraine
bench --site <staging-site> install-app erpnext_ua
bench --site <staging-site> migrate
```

Оновлення встановленого app:

```bash
cd apps/erpnext_ua
git fetch origin
git checkout <approved-release-commit>
cd ../..
bench --site <staging-site> migrate
```

Read-only schema/configuration check:

```bash
bench --site <staging-site> execute \
  erpnext_ua.consignment_and_commission.services.diagnostics.collect_environment
```

Gate: `status` дорівнює `ready_for_acceptance`, `blocking_checks` порожній.
Перевірки `psbo_migration` навмисно блокують upgrade, якщо старі проведені
receipts/sales не мають вхідної вартості та рухів 024. Застосунок не вгадує
історичну вартість автоматично: бухгалтер має надати її за актами приймання,
а міграція історичних документів виконується й звіряється окремо на staging.

## 4. Обов'язкова конфігурація

1. У формі `CC Account Mapping` виберіть Company та натисніть
   `Apply Ukrainian PSBO Mapping`. Для повного плану буде прив'язано
   024/702/703/704/685; для спрощеного — 024/70.1/70.2 і позначені технічні
   розширення 70.3/68.6/68.7. Перевірте також default Supplier payable.
2. Створіть `CC Location` і прив'яжіть тільки технічні Warehouses потрібної
   Company.
3. Створіть `CC Partner Profile` для Supplier та чинні `CC Contract` з
   методом, currency, rate/due-date/fiscal policy.
4. Перевірте Item stock UOM, Serial/Batch options, Customer, Supplier payable
   currency, enabled Selling Price List для кожної sale currency, Mode of
   Payment, multiple-item flags у Buying/Selling Settings та дозволи Company
   для користувачів.
5. Призначте мінімальні ролі: `Commission Trade User`, `Commission Trade
   Manager`, `Commission Trade Auditor`.
6. Налаштуйте зовнішній POS/print provider. Core queue не підтверджує юридичну
   фіскалізацію без успішної відповіді provider.
7. Залиште `CC Settings.enabled = 0` до завершення smoke acceptance.

## 5. Staging smoke acceptance

На тестових Item/Supplier/Customer проведіть і повністю сторнуйте:

- по одному receipt кожного з чотирьох методів одного Item у різний час;
- резерв і продаж quantity, що перетинає кілька lots; у `CC FIFO Inventory` та
  Sales Invoice allocation порядок має бути за received datetime, а не методом;
- commission sale: `commission + partner debt = net sale`;
- consignment sale: `partner rate × qty + retained = net sale`;
- receipt стороннього товару: Increase 024 дорівнює кількості × вартості акта,
  вираженої у функціональній валюті Company;
- sale стороннього товару: Decrease 024; 702/704/703/685 залишають чистим
  доходом лише винагороду, а 902/COGS відсутній;
- customer return відновлює точну кількість і вартість 024;
- partner return і ownership conversion зменшують 024; після conversion у
  `OWN` подальший продаж використовує стандартні дохід і COGS;
- OWN receipt: Stock Asset і Supplier payable дорівнюють Purchase Invoice;
- partial settlement payments у contract currency з різними exchange rates;
- customer return до settlement і після settlement;
- exact Serial/Batch sale, partner return та ownership conversion;
- retry того самого idempotency key без дублювання документів;
- два конкурентні reservations одного залишку: успішний лише один;
- POS split, print failure/retry та компенсацію.

Після smoke запустіть:

```bash
bench --site <staging-site> execute \
  erpnext_ua.consignment_and_commission.integrations.reconciliation.audit_financial_integrity
```

Gate: `issue_count = 0`. Звірте Trial Balance, Stock Balance, Accounts Payable,
`CC Partner Balance`, `CC Sale Financials` та `CC Financial Integrity`.

## 6. Production rollout

У погоджене maintenance window:

```bash
bench --site <site> backup --with-files
cd apps/erpnext_ua
git fetch origin
git checkout <approved-release-commit>
cd ../..
bench --site <site> migrate
bench --site <site> execute \
  erpnext_ua.consignment_and_commission.services.diagnostics.collect_environment
bench restart
```

Після green diagnostics перенесіть перевірену конфігурацію, повторіть read-only
звіти й активуйте спочатку одну Company/Location. Увімкніть потрібні методи та
`CC Settings.enabled` тільки після підписаного acceptance.

## 7. Моніторинг

Під час пілота контролюйте щонайменше:

- `CC Financial Integrity`: нуль issues;
- `CC POS Queue`: немає завислих `PROCESSING`, exhausted retries або
  непоясненого manual review;
- прострочені `CC Allocation` звільняються scheduler;
- `CC Partner Balance` збігається з Settlement Reports, Purchase Invoices і
  Accounts Payable;
- Stock Ledger quantity за `CC Stock Lot` збігається з `CC FIFO Inventory`;
- `UA Off Balance Statement` за 024 збігається з відкритими сторонніми lots за
  партнером, Item, Warehouse і Serial/Batch;
- workers/scheduler online, error log і provider callbacks без повторних
  фінансових документів.

## 8. Rollback

Якщо код встановився, але операції ще не активувалися:

1. залиште `CC Settings.enabled = 0`;
2. поверніть approved previous app commit;
3. виконайте `bench --site <site> migrate` і `bench restart`;
4. повторіть diagnostics.

Якщо вже є проведені операції, не робіть `uninstall-app` і не видаляйте custom
fields/DocTypes. Спочатку зупиніть нові операції feature gate, зафіксуйте
financial-integrity/queue snapshots і виконайте контрольоване скасування у
зворотному порядку: Payment Entry → adjustment/debt JE → Settlement Report →
return/sale → ownership conversion/partner return → source receipt. Документи з
captured або невідомим зовнішнім payment state переводяться у manual review.

Якщо міграція пошкодила schema/data або контрольоване сторнування неможливе,
відновіть повний pre-deploy database/files backup у maintenance window. Restore
має повертати також exact apps commit. Після restore повторіть standard ERPNext
ledger checks перед відкриттям користувачам.

## Ізольований test overlay

`frappe-test.override.yml` монтує checkout лише в окремий compose project і не
призначений для production. Він додає app у `PYTHONPATH`, worker і scheduler для
локальної Frappe integration regression.
