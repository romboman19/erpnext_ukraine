# Gap analysis

До цієї зміни POS-UA мав одну `discount_amount`, не мав bonus reservation, signed balance, окремої накопичувальної метрики та item-level bonus provenance. Стандартний ERPNext Loyalty не підходить через прив’язку до Company, обмежену return semantics і відсутність потрібної POS saga.

Реалізовано:

- розклад `non_loyalty_discount_amount + loyalty_redeemed_amount`;
- Scope/Location/Program/Card Type/Tier/eligibility/snapshot;
- Account, Card, bonus/metric ledgers, lots, expiry obligations та allocations;
- quote/reserve/payment lease/release/consume;
- Sales Invoice posting, partial/repeated returns і cancellation inverse entries;
- pending activation, expiry без debt, reconciliation, audited adjustments та opening import;
- POS UI, fiscal snapshot, workspace і основні операційні звіти.

Свідомі межі V1: автоматичний intercompany settlement лишається `REPORT_ONLY`; ecommerce adapter, повідомлення клієнту, автоматична зміна Card Type та inactivity policy не активовані. GSF і FOP routing лишаються власниками складської вартості, партій і вибору продавця.
