# Gate 0D evidence — accounting, settlement and payments

> Historical spike evidence only. Its provisional consignment-COGS recognition
> was replaced by the Ukrainian P(С)БО model in
> [ADR 0008](../../adr/0008-ukrainian-psbo-commission-accounting.md).

Date: 2026-07-13  
Site: `postest.local`  
Company: `POS Test Ukraine`  
Result: `PASS WITH REQUIRED APPLICATION GUARDS`

Production was not opened, migrated, restarted or written. All transactional
fixtures below were submitted on the isolated test site and then cancelled.
Temporary Currency Exchange fixtures were deleted after cancellation.

## Reproducible runner

```bash
bench --site postest.local execute \
  erpnext_consignment_and_commission.consignment_and_commission.spikes.accounting.run_accounting_flow \
  --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0B","company":"POS Test Ukraine"}'
```

The runner is not imported by hooks or exposed as an API. It retains the same
site, Company and explicit-write allow-list as the previous Stage 0 probes.

## Recognition Journal Entries

Commission fixture `ACC-JV-2026-00010`:

| Account | Debit UAH | Credit UAH |
| --- | ---: | ---: |
| TP Commission Gross Proceeds - PTU | 10,000 | 0 |
| TP Commission Revenue - PTU | 0 | 1,500 |
| TP Unreported Commission Liability - PTU | 0 | 8,500 |

Consignment fixture `ACC-JV-2026-00011`:

| Account | Debit UAH | Credit UAH |
| --- | ---: | ---: |
| TP Consignment COGS - PTU | 8,000 | 0 |
| TP Unreported Consignment Liability - PTU | 0 | 8,000 |

`ACC-JV-2026-00012` repeated the consignment recognition before the USD
settlement path. Every recognition JE balanced in company currency.

## UAH report, debt and partial payments

- Report: `TP-GATE-0D-UAH-F39439BB5B`.
- Debt JE: `ACC-JV-2026-00013`.
- Supplier row: Creditors `8,500 UAH`, `party_type=Supplier`,
  `party=TP Gate 0D Supplier UAH`.
- PE `ACC-PAY-2026-00005`: `3,000 UAH`; outstanding became `5,500 UAH`.
- PE `ACC-PAY-2026-00006`: `5,500 UAH`; outstanding became `0 UAH`.

Both Payment Entry documents pointed to the same standard debt JE and the same
custom report link. This confirms one report to many partial PE.

The native document model has no invariant preventing references to debt JE
from different custom reports. The application guard rejected a draft with two
reports with:

```text
Payment Entry references must belong to exactly its linked settlement report
```

This guard is mandatory in the production Payment Entry `validate` hook.

## USD report and exchange differences

- Report: `TP-GATE-0D-USD-F39439BB5B`, obligation `200 USD`.
- Provisional debt rate: `40.00 UAH/USD`.
- Debt JE: `ACC-JV-2026-00015` — Dr unreported liability `8,000 UAH`,
  Cr Supplier Payable `200 USD / 8,000 UAH`.
- PE `ACC-PAY-2026-00007`: `100 USD`, rate `41.20`, bank credit `4,120 UAH`.
- PE `ACC-PAY-2026-00008`: `100 USD`, rate `41.50`, bank credit `4,150 UAH`.
- Currency outstanding changed `200 → 100 → 0 USD`.
- Actual company-currency payment total was `8,270 UAH`.

ERPNext generated and submitted two system Journal Entries:

| JE | Payment | Exchange loss |
| --- | --- | ---: |
| ACC-JV-2026-00016 | ACC-PAY-2026-00007 | 120 UAH |
| ACC-JV-2026-00017 | ACC-PAY-2026-00008 | 150 UAH |

Each JE credited the base value of the USD payable and debited
`Exchange Gain/Loss - PTU`. Payment Ledger preserved `200`, `-100`, `-100` in
account currency independently from the UAH base values `8,000`, `-4,120`,
`-4,150`.

The spike also confirmed an ERPNext constraint: the USD Supplier must have
`default_currency=USD` when its default payable account is in USD.

## Backdated adjustment

Fixture `ACC-JV-2026-00018` retained:

- economic date `2026-06-03`;
- simulated policy closed-through date `2026-07-03`;
- actual posting date `2026-07-04`, the first open date;
- Dr Consignment COGS / Cr Unreported Consignment Liability `3 UAH`.

The probe did not modify global Accounts Settings or freeze the shared test
Company. It verified the application date resolver and the actual JE posting on
the resolved open date. Closed-period authorization remains a Stage 1 settings
and permissions concern.

## Cancellation and cleanup

The verified order was:

1. Payment Entry;
2. adjustment JE;
3. debt JE;
4. Settlement Report;
5. recognition JE.

Immediately after cancelling the PE documents, before cancelling debt JE:

- `ACC-JV-2026-00013` outstanding returned to `8,500 UAH`;
- `ACC-JV-2026-00015` outstanding returned to `200 USD`;
- exchange JE `ACC-JV-2026-00016` and `ACC-JV-2026-00017` automatically became
  cancelled (`docstatus=2`).

All four PE, three debt JE, three reports, three recognition JE and the
backdated adjustment were then cancelled with no cleanup error. Persistent
test-only accounts, Suppliers, custom fields and cancelled documents remain as
auditable spike evidence; they are not production configuration.

## Implementation constraints carried forward

1. Production code resolves semantic account keys through Company settings.
2. The custom report link belongs on JE/PE parent documents; standard JEA
   reference fields stay empty so Payment Entry can match the debt JE.
3. The app uses its own PE builder instead of relying on generic
   `get_payment_entry` inference for a custom JE source.
4. Obligation-currency outstanding is reconciled with Payment Ledger.
5. One-report-per-PE is a server-side invariant, not a UI convention.
6. Economic and posting dates are stored separately for backdated revisions.

See [ADR 0004](../../adr/0004-settlement-accounting-and-payments.md).
