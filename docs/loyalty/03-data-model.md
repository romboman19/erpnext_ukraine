# Data model

- `UA Loyalty Scope`: економічна межа спільного балансу; не Company.
- `UA Loyalty Location`: temporal mapping POS Cash Desk/warehouse/branch/FOP до Scope.
- `UA Loyalty Program`, `Tier`, `Eligibility Rule`, `Rule Snapshot`: версійні правила.
- `UA Loyalty Account`: cache для одного `(Customer, Scope)`; source of truth — ledgers.
- `UA Loyalty Card`: UUID/barcode і lifecycle без hard delete.
- `UA Loyalty Ledger Entry`: signed active/pending рухи.
- `UA Loyalty Metric Entry`: окрема накопичувальна метрика tier.
- `UA Loyalty Allocation`: зв’язок руху з item/batch/serial/original return row.
- `UA Loyalty Bonus Lot` та `Expiry Obligation`: activation/expiry provenance.
- `UA Loyalty Reservation`: конкурентний резерв balance до payment.
- `UA Loyalty Adjustment`, `Import Batch`, `Account Change Log`: контрольовані операції й аудит.

Унікальні DB constraints захищають `(customer, scope)`, barcode, snapshot hash та idempotency keys. Ledger/metric/allocation/change-log записи append-only; виправлення — inverse entry.
