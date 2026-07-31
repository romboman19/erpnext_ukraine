# State machines

Reservation:

```text
ACTIVE -> PAYMENT_IN_PROGRESS -> CONSUMED
   |             |
   +-> RELEASED  +-> RELEASED(manager recovery)
   +-> EXPIRED
```

POS loyalty state:

```text
IDENTIFIED -> QUOTED -> RESERVED -> PAYMENT_IN_PROGRESS -> POSTED
                    \-> REQUIRES_REQUOTE
```

Bonus lot: `PENDING -> ACTIVE -> DEPLETED/EXPIRED/REVERSED`. Pending return до activation зменшує pending amount; scheduler активує лише residual.

Adjustment: `DRAFT -> PENDING_APPROVAL -> APPROVED -> POSTED` або `REJECTED`. Для суми від configured threshold requester не може бути approver.

Import: `DRAFT -> RUNNING -> COMPLETED`, `DRY_RUN_COMPLETE` або `FAILED`. Завершені batch immutable.
