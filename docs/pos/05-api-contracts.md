# 05 — API-контракти

## 1. Fiscal Adapter (Python, `ua_pos/adapters/fiscal.py`)

Абстракція над провайдером фіскалізації. Реалізація фази 1 — `DPSFiscalAdapter`
(обгортає існуючі `ua_fiscal.fiscal_client` + `xml_builder` + персистентність у PRRO Receipt).
Майбутні реалізації (Checkbox тощо) — той самий інтерфейс.

```python
class FiscalAdapter(ABC):
    # --- стан ---
    def server_status(self) -> ServerStatus:                # ДПС доступний? офлайн дозволений?
    def register_status(self, register: str) -> RegisterStatus:
        # {shift_open: bool, shift_opened_at, offline_session: bool,
        #  last_doc_local_num, last_doc_hash, errors: []}

    # --- зміна ---
    def open_shift(self, register: str, operator: OperatorRef,
                   idem_key: str) -> FiscalDocResult
    def close_shift(self, register: str, operator: OperatorRef,
                    idem_key: str) -> ZReportResult          # Z-звіт
    def x_report(self, register: str, operator: OperatorRef) -> XReportResult

    # --- службові ---
    def service_deposit(self, register: str, amount: Money,
                        operator: OperatorRef, idem_key: str) -> FiscalDocResult
    def service_withdrawal(self, register: str, amount: Money,
                           operator: OperatorRef, idem_key: str) -> FiscalDocResult

    # --- чеки ---
    def fiscalize_sale(self, receipt: ReceiptPayload, idem_key: str) -> FiscalDocResult
    def fiscalize_return(self, original: FiscalRef, receipt: ReceiptPayload,
                         idem_key: str) -> FiscalDocResult   # повне/часткове — за складом позицій
    def storno(self, original: FiscalRef, operator: OperatorRef,
               idem_key: str) -> FiscalDocResult             # ORDERSTORNUM; NotSupportedError якщо ні

    # --- результат / ідемпотентність ---
    def get_result(self, idem_key: str) -> FiscalDocResult | None
        # обов'язковий виклик перед будь-яким ретраєм (сценарій «зв'язок втрачено після успіху»)
    def receipt_view(self, fiscal_ref: FiscalRef) -> ReceiptView   # текст/QR/лінк е-чека, для повторного друку

    # --- офлайн-сесія ---
    def begin_offline(self, register: str, idem_key: str) -> OfflineSessionRef
    def end_offline(self, session: OfflineSessionRef, idem_key: str) -> None
    def flush_offline(self, session: OfflineSessionRef) -> PackageResult   # /pck
```

DTO (основні):

```python
ReceiptPayload:
  register: str            # PRRO Cash Register
  fop_profile: str
  operator: OperatorRef    # ФІО касира + КЕП (UA KEP Key)
  items: [ {code, name, uom, qty, price, amount, discount_sum?, letters?} ]
  payments: [ {payformcd: int, name: str, sum, provided?, remains?} ]
  total: Money
  testing: bool            # PRRO Settings.mode

FiscalDocResult:
  status: "delivered" | "rejected" | "pending" | "offline_queued"
  fiscal_number: str | None      # ORDERTAXNUM
  local_number: int
  qr: str | None
  raw_response_ref: str          # PRRO Receipt name (журнал)
  error: {code, message} | None
```

Гарантії реалізації:
- кожен виклик з idem_key спершу шукає PRRO Receipt за idem_key: якщо Delivered — повертає збережений
  результат; якщо Sending/TransportError — виконує QueryState, а не новий документ;
- підписаний XML зберігається і повторно надсилається байт-у-байт (UID незмінний);
- усі помилки типізовані: `FiscalUnavailable`, `FiscalRejected(code)`, `FiscalNotSupported`,
  `OfflineNotAllowed` — POS показує касиру локалізоване пояснення, не traceback.

## 2. Terminal Adapter (Python, `ua_pos/adapters/terminal.py`)

Реалізація — `PrivatPosAdapter` безпосередньо у `erpnext_ua.ua_pos.adapters.terminal`
(gateway `pb-pos-gateway`). Так касова транзакція, її стан та recovery версіюються атомарно.

```python
class TerminalAdapter(ABC):
    def ping(self, terminal: str) -> bool                       # /verify
    def sale(self, terminal: str, amount: Money,
             operation_id: str) -> TerminalResult
    def refund(self, terminal: str, amount: Money, operation_id: str,
               reference: str) -> TerminalResult                # reference = rrn/invoiceNumber продажу
    def void(self, terminal: str, operation_id: str,
             reference: str) -> TerminalResult                  # скасування до закриття бізнес-дня
    def status(self, terminal: str, operation_id: str) -> TerminalResult
        # ЄДИНИЙ дозволений вихід зі стану timeout/unknown
    def last_transactions(self, terminal: str, date) -> list[TerminalResult]  # для звірки
    def settlement(self, terminal: str) -> dict                 # звірка підсумків дня (якщо підтримує ECR)

TerminalResult:
  status: "confirmed" | "declined" | "cancelled" | "timeout" | "unknown"
  rrn, invoice_number, auth_code, card_mask, amount, currency
  receipt_text: str | None        # чек термінала для друку/архіву
  raw: dict                       # masked
```

**Обов'язкові доробки `pb-pos-gateway` (Go) до фази 3:**
1. `POST /status` — пошук транзакції за `operation_id`/останні транзакції (журнал ECR / ServiceMessage
   протоколу ПБ) — без цього стан `unknown` нерозв'язний автоматично;
2. приймати і зберігати `operation_id` у legacy `/purchase`, `/refund` (зараз ігнорується);
3. `POST /void`;
4. ідемпотентність на боці gateway: повтор `operation_id` не шле нову команду на ECR, а віддає результат;
5. `GET /health` (зараз тільки /verify по терміналу).

## 3. Друк: server-side + (опційно) hardware agent

**Базова схема (обрана): без агента — усі принтери мережеві.**
Парк обладнання (підтверджено власником, усе в LAN/WiFi):

| Принтер | Роль | Транспорт |
|---|---|---|
| Xprinter XP-80T | чековий (фіскальні/нефіскальні/службові чеки) | TCP :9100, сирий ESC/POS (python-escpos) |
| HP LaserJet M135w | офісний (рахунки, накладні, звіти, гарантійні талони) | CUPS/IPP або PDF-рендер |
| XP-450B #1 | етикетки-цінники | TCP :9100, TSPL/ESC-label |
| XP-450B #2 | етикетки ТТН (Нова Пошта) | TCP :9100 |
| ~~Zebra~~ | цінники — **не працює, на продаж, не інтегрується** | — |

Воркер черги POS Print Job відкриває TCP і шле payload за типом принтера; heartbeat — TCP-probe.
Друк цінників/ТТН — теж через POS Printer (тип `label`); ТТН-етикетки Нової Пошти вже генерує
`ukrainian_integrations` (shipment/nova_poshta) — POS лише маршрутизує на потрібний XP-450B.
Жодного ПЗ на касових ПК не потрібно (сканер — keyboard wedge).

**Fallback: `pos-hw-agent`** (якщо принтери USB-only — blocking question №2).
Малий сервіс (Go/Node — у стилі pb-pos-gateway) на кожному касовому ПК:

```
Реєстрація:  адміністратор генерує pairing-код у POS Cash Desk →
             агент викликає POST /api/method/...hw.pair {code, hostname, fingerprint}
             → отримує agent_token (зберігається локально), запис у Desk.device_tokens

Канал:       agent → ERP long-poll/WebSocket (socket.io):  GET commands?since=...
             (вихідне з'єднання від агента — без відкритих портів на касі)

Команди:     {command_id (uuid), type: print_escpos|print_pdf|open_drawer|status,
              payload, created_at}
             агент виконує ІДЕМПОТЕНТНО (журнал command_id, повтор → done з кешу)
             → POST result {command_id, status: done|failed, error}

Heartbeat:   кожні 30с: agent alive + статуси принтерів (paper_out, offline)
Безпека:     agent_token у заголовку; токен відкликається з Desk; TLS до ERP
Відновлення: reconnect з backoff; недоставлені команди живуть у POS Print Job (queued)
```

POS Print Job — спільна черга для обох схем (воркер сам обирає транспорт за типом принтера).

## 4. POS REST API (whitelisted, `erpnext_ua.ua_pos.api.*`)

Загальне: усі мутації — POST з `idem_key`; усі виклики — з `pos_session_token`;
відповіді — типізовані помилки `{error_code, message_uk, details}`; версіювання `?v=1`.

```
# Сесія / допуск
POST session.login_by_barcode   {desk, device_token, barcode}     → {session_token, employee, permissions[]}
POST session.logout             {}                                 
POST session.handover           {to_barcode}                       → підтвердження двох сторін
GET  session.state              {}                                 → зміна, ФОП-статуси, health обладнання

# Управлінська зміна
POST shift.open                 {denominations[], idem_key}
POST shift.close_begin          {}                                 → очікувані залишки по валютах
POST shift.close_confirm        {denominations[], comment?, manager_code?, idem_key}
POST shift.cash_expense / cash_transfer.create / cash_transfer.receive / cash_deposit ...

# Кошик
POST order.create               {customer?}                        → order_id
POST order.scan                 {order_id, query, qty?}            → item resolve (пріоритет: точний ШК →
                                                                     точний артикул → префікс → назва → атрибути)
POST order.set_qty / set_serial / set_batch / set_discount / set_customer / hold / resume / cancel
GET  order.get                  {order_id}                         → повний стан + пояснення роутингу ФОП

# Checkout (saga)
POST checkout.validate          {order_id}                         → список проблем або ok + спліт по ФОП
POST checkout.start             {order_id, payments[], idem_key}   → стан
POST checkout.cash_confirm      {order_id, attempt, received}      
POST checkout.card_status       {order_id, attempt}                → polling unknown
GET  checkout.state             {order_id}                         → поточний стан saga (для відновлення)
POST checkout.resolve_manual    {order_id, attempt, resolution, manager_code}

# Повернення
POST return.lookup              {token}                            → продаж + доступні до повернення
POST return.start               {order_id, lines[], refunds[], idem_key}

# Рахунки/видача
POST invoice.create             {order_id → Sales Order}           # рахунок без списання
POST invoice.take_payment       {sales_order, amount, mode, idem_key}
POST invoice.issue_goods        {sales_order, lines[], idem_key}   # видача, блок повторної видачі

# Фіскальне меню
POST fiscal.open_shift / close_shift / x_report / service_in / service_out   {register, idem_key}
GET  fiscal.status              {desk}                             → по кожному ФОП/ПРРО каси

# Довідково
GET  stock.query                {item, warehouse?}                 # права: свій склад / усі
GET  reports.shift_cash / shift_goods / sales_journal {filters}    # обмеження періоду за роллю
POST manager.issue_code         {action_type, scope}               # з-під ролі менеджера
```

Realtime (socket.io): канал `pos:{desk}` — зміни стану order/attempt/print job, health-статуси;
UI не поллить, крім `card_status` у стані unknown.

## 5. Контракт ідентифікації клієнта (зовнішній модуль)

POS не реалізує ідентифікацію, лише викликає стабільний контракт:

```
POST customer.identify.begin  {channel: sms|call|telegram, phone}  → {request_id}
POST customer.identify.confirm {request_id, code?}                 → {customer_id, verified: bool}
POST customer.quick_create     {name, phone, loyalty_optin}        → {customer_id}
GET  customer.loyalty_state    {customer_id}                       → {points, allowed_redeem, program}
```

Реалізація каналів — `erpnext_ukraine_integrations/customer_identification`:
TurboSMS OTP, Telegram contact deep-link і контрольний вхідний дзвінок через VitalPBX.
POS використовує один контракт і не залежить від деталей конкретного каналу.
