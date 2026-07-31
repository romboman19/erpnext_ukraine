# Permissions and audit

- `UA Loyalty User`: POS read/use.
- `UA Loyalty Manager`: account/card operations та request/approval у дозволених межах.
- `UA Loyalty Auditor`: read-only ledgers, snapshots, reports і change log.
- `UA Loyalty Administrator`: settings, publication, imports, repair і setup.

Ledger, metric, allocation, snapshot та account change log створюються тільки service context і не видаляються. Account cache balance не редагується формою. Card delete заборонений; block/lost/replace/close створюють change log.

Reconciliation repair не є тихим: спочатку API повертає mismatch, а mutation потребує Administrator. Проведені adjustment не cancel-яться — використовується inverse adjustment.
