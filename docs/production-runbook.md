# Production runbook: UA POS + ПРРО

## 1. Передумови

- Frappe/ERPNext v16, окремі worker/scheduler і Redis;
- HTTPS для ERPNext, MariaDB backup, синхронізований час на всіх вузлах;
- `erpnext_ukraine_prro_signer` 0.2.x лише у приватній Docker-мережі;
- зареєстровані господарська одиниця, ПРРО, касир/печатка та чинний КЕП;
- мережевий ESC/POS-принтер у приватній LAN, для термінала — працездатний
  `pb-pos-gateway` і унікальний `operation_id`.

## 2. Backup та оновлення

```bash
bench --site <site> backup --with-files
bench get-app https://github.com/romboman19/erpnext_ukraine
bench --site <site> migrate
bench --site <site> clear-cache
```

Не оновлюйте застосунок посеред відкритої фіскальної/offline-сесії. Перед
оновленням дочекайтеся, щоб `PRRO Receipt` не мав `Sending/Uncertain`, а
`PRRO Offline Session` — `Open/Queued/Sending/Error`.

## 3. Signer

Згенеруйте окремий секрет щонайменше 32 символи. Не публікуйте порт 8080 назовні.

```bash
docker build -t erpnext-ukraine-prro-signer:0.2 .
docker run -d --restart unless-stopped --name prro-signer \
  --network <private-frappe-network> \
  -e API_KEY='<random-secret-at-least-32-chars>' \
  erpnext-ukraine-prro-signer:0.2
```

У `PRRO Settings` задайте `http://prro-signer:8080`, той самий API key, timeout
15–30 секунд і спочатку режим `Тестовий`. КЕП зберігайте як Private File;
пароль — тільки в Password field `UA KEP Key`.

## 4. ERPNext configuration

1. Заповніть `FOP Profile` і перевірте РНОКПП/ЄДРПОУ.
2. Створіть `UA KEP Key`, перевірте власника та `Valid Until`.
3. Створіть `PRRO Cash Register`: фіскальний номер, локальний номер реєстратора
   з форми 1-ПРРО, точна назва/адреса ГО, FOP, default key. `Device ID`
   генерується один раз як 64-символьний SHA-256 і не переноситься між пристроями.
4. Для кожного `Mode of Payment` вкажіть `PRRO Payment Form Code`.
5. Для ПДВ/акцизу заповніть у `Sales Taxes and Charges` PRRO Type/Letter/Name,
   а в Item — `PRRO Tax Letters`; УКТЗЕД і ДКПП заповнюйте там, де це вимагає закон.
6. Створіть `POS Printer` з приватною IP, портом 9100, code page принтера
   (типово encoding `cp866` / code_page `17` — PC866 — для XP-80T/XP-58; якщо на
   конкретному екземплярі є таблиця Windows-1251, спробуйте `cp1251`/`23`).
   **Обов'язково зробіть тестовий чек одразу після налаштування** — таблиця кодових
   сторінок відрізняється між партіями/прошивками навіть одного принтера, і чек
   з кирилицею, надрукований ієрогліфами, означає, що code_page підібрано невірно.
   Прив'яжіть принтер до `POS Cash Desk`.
7. Прив'яжіть до каси склад, клієнта за замовчуванням, ПРРО, КЕП і банківський термінал.
8. Надайте співробітникам `Employee Cash Desk Access`; права адміністратора касиру не давайте.

## 5. Go-live checklist

На staging/test ПРРО виконайте і звірте:

- `ServerState`, `DeviceRegister`, `TransactionsRegistrarState`;
- відкриття зміни, готівковий і картковий продаж, змішану оплату;
- повне й часткове повернення з первинним чеком;
- службове внесення/видачу, Z-звіт і закриття;
- фізичний друк: українські `і/ї/є/ґ`, QR, обрізання, копія;
- timeout термінала: лише status/recovery, без другого sale;
- відключення ДПС після успішної передачі: offline begin з revoke, CRC/hash-chain,
  пакет і повернення online;
- суми: касова скринька = підтверджені payments = Sales Invoice = Z = settlement ECR.

Лише після цього змініть `PRRO Settings.mode` на `Робочий`. Не створюйте
експериментальні документи в робочому режимі.

## 6. Моніторинг

Алерт потребують:

- `PRRO Receipt`: `Uncertain` або `Error`;
- `PRRO Cash Register`: `Blocked` (розбіжність Device ID, `NextLocalNum`, стану
  зміни або TESTING). Після усунення причини виконайте `DeviceRegister`, а тоді
  `sync_register_state`; успішна звірка штатно повертає касу в `Online`;
- `PRRO Offline Session`: `Error/Blocked`, наближення до 36/168 годин;
- `POS Order`: `Payment Unknown`, `Fiscal Pending`, `Manual Review`,
  `Completed Print Error`;
- `POS Print Job`: `Failed` або `Printing` довше 10 хвилин;
- `Terminal Transaction.reconciliation_status`: `Mismatch/Manual`;
- signer health не `200`, прострочення КЕП, scheduler/worker stopped.

Scheduler кожні 5 хвилин відновлює ПРРО, щохвилини — print queue. Після аварії
не повторюйте `/doc`, terminal sale або stale print вручну навмання: спочатку
виконайте reconciliation. Для `Error` кнопка повтору спочатку запитує
`DocumentInfoByLocalNum` і `TransactionsRegistrarState`; номер повертається
allocator-у лише коли ДПС підтвердила, що його не спожито. Повторний чек
друкуйте лише як «КОПІЯ».

## 7. Rollback

Код можна відкотити до попереднього образу після зупинки worker/scheduler, але
схему БД і фіскальний ledger не видаляйте. Якщо migration несумісна, відновіть
повний site backup у нову базу й перевірте його до перемикання трафіку. Жоден
прийнятий ДПС документ, terminal transaction або print audit не видаляється.
