# Stage 3 — sale and POS

## Allocation contract slice

Перший Stage 3 slice фіксує бізнес-контракт майбутнього live allocation без
підключення транзакційних Sales Invoice/POS hooks.

Один Item може одночасно мати чотири джерела:

| Source method | Ownership model | Technical warehouse |
|---|---|---|
| `BUYOUT` | `OWN` | Own |
| `DEFERRED_PURCHASE` | `OWN` | Own |
| `COMMISSION` | `COMMISSION` | Commission |
| `CONSIGNMENT` | `CONSIGNMENT` | Consignment |

Викуп і відстрочка відрізняються умовами розрахунку з постачальником, але не
ownership. Їхні окремі надходження мають власні FIFO timestamps і source
snapshots. Спосіб або строк оплати не дає пріоритету під час продажу.

## FIFO invariants

Allocator застосовує:

1. exact scanned/selected Serial No;
2. required Batch filter;
3. global FIFO між усіма дозволеними джерелами за
   `(fifo_datetime, receipt_name, receipt_row_index, lot_name)`.

Candidate filtering відкидає іншу Location, заборонені Warehouses, blocked або
pending-transfer lots, zero/unavailable balance, reserved quantity та
несумісний fiscal route. Allocation slice зберігає source method, ownership,
Warehouse, lot, Batch/Serial і стабільні FIFO coordinates.

## Verification

Isolated tests підтверджують єдиний FIFO для чотирьох source methods, відсутність
payment-method priority, stable tie-breakers, Serial/Batch priority, reserved
quantity exclusion, insufficient stock і source/ownership validation.

## Read-only CC Stock Lot adapter

Другий Stage 3 slice додає production adapter для classified stock без
транзакційних записів:

- вибирає лише `OPEN`/`BLOCKED` lots потрібних Company, Location, Item і
  дозволених Warehouses;
- агрегує active balance безпосередньо зі Stock Ledger Entry за ownership
  Inventory Dimension та Warehouse;
- переносить immutable receipt datetime, receipt/row tie-breakers, relationship
  model, fiscal policy, reserved quantity і tracking identity;
- Batch lot стає одним quantity candidate;
- Serial lot стає одним candidate на фактично активний Serial No у Warehouse з
  тим самим immutable owner;
- невідповідність ledger balance, Serial masters або audit identity завершує
  preview контрольованою помилкою, а не неповною allocation.

Adapter реалізує спільний `CandidateAdapter` contract. Read-only orchestrator
об'єднує результати кількох adapters і лише після цього виконує global FIFO.
Публічний API поки не відкрито до завершення atomic reservation.

## OWN receipt and candidate slice

Третій Stage 3 slice додає контрольоване приймання власного товару:

- `CC Own Receipt` підтримує `BUYOUT` і `DEFERRED_PURCHASE`;
- submit створює стандартний `Purchase Invoice` з `update_stock=1`, stock asset,
  Supplier payable, currency/conversion rate і due date;
- BUYOUT має due date у день приймання, DEFERRED_PURCHASE вимагає майбутню дату;
- кожен рядок отримує immutable OWN `CC Stock Lot` та Inventory Dimension у
  Purchase Invoice Item/SLE;
- Batch/Serial master отримує той самий owner і exact identity preview;
- linked Purchase Invoice скасовується лише через `CC Own Receipt`;
- ненульовий нерозмічений balance у дозволеному CC Warehouse блокує preview.

Тепер один adapter повертає всі чотири source methods і передає їх одному
global FIFO allocator.

## Persistent atomic reservation slice

Четвертий Stage 3 slice перетворює preview на durable hold:

- `CC Allocation` має унікальний idempotency key, fingerprint запиту, TTL і
  стани `PENDING`, `RESERVED`, `CONSUMED`, `RELEASED`, `EXPIRED`;
- immutable `CC Allocation Slice` зберігає точні lot, Warehouse, source method,
  ownership, Batch/Serial та FIFO coordinates;
- lot rows блокуються в детермінованому порядку, balance повторно читається зі
  Stock Ledger, а `reserved_qty` змінюється conditional SQL update;
- exact Serial No додатково захищений identity-level lock;
- повтор того самого запиту повертає ту саму allocation, а інший payload з тим
  самим ключем завершується контрольованою помилкою;
- release, TTL expiry та consumption атомарно звільняють агрегат, terminal
  allocation лишається audit evidence;
- scheduler звільняє прострочені holds, а schema migration створює індекси для
  expiry та serial lookup.

Reservation є окремою транзакційною межею і повинна бути першою write-операцією
checkout-запиту. Це дає безпечний full rollback/retry для MariaDB-конфлікту двох
одночасних однакових idempotent inserts без втрати сторонніх записів.

Двопроцесні probes підтверджують: для різних ключів останню одиницю отримує лише
один contender; для однакового ключа обидва процеси отримують одну й ту саму
allocation, а фінальний `reserved_qty` дорівнює `1`, не `2`.

## Межа поточного slice

Цей контракт ще не робить звичайний Sales Invoice або POS Order глобально FIFO.
До активації live sale потрібні:

1. preview/confirm API, що розбиває один логічний Item на allocation slices;
2. server-owned Sales Invoice Item snapshots і Stock Entry/Sales Invoice guards;
3. clean-site integration test із чотирма надходженнями одного Item, частковим
   продажем, cancel/reuse, return та concurrent last-unit conflict.

Для нетрекінгового OWN товару ERPNext COGS залишається warehouse-level FIFO.
Точна lot-level valuation identity потребує ERPNext-supported Batch/Serial
valuation та окремого integration proof.

Evidence: [`read-only candidate preview`](evidence/2026-07-13-read-only-candidate-preview.md).

Evidence: [`OWN receipt and four-source candidates`](evidence/2026-07-13-own-receipt-and-four-source-candidates.md).

Evidence: [`atomic FIFO reservation`](evidence/2026-07-13-atomic-fifo-reservation.md).
