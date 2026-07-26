# ADR 0004 — Settlement accounting and Payment Entry boundary

## Status

Accepted after Gate 0D spike on 2026-07-13. Recognition entries were superseded
by [ADR 0008](0008-ukrainian-psbo-commission-accounting.md); the settlement and
Payment Entry boundary in this ADR remains active.

## Context

Third-party sales need two distinct accounting moments:

1. recognition after the customer sale, without a payable to a named Supplier;
2. official Supplier debt after Settlement Report submit.

The debt can be in a contract currency, one report can have multiple partial
payments, and every Payment Entry must belong to exactly one report. A
backdated economic event must not rewrite a closed period.

ERPNext v16 can use a party row in Journal Entry as an outstanding document in
Payment Ledger. Its standard Journal Entry account `reference_type` cannot be
used for the custom Settlement Report, however: a populated custom reference
would make the party row unavailable to standard Payment Entry matching.

The generic `get_payment_entry` helper also does not fully describe this custom
Journal Entry source. For a Journal Entry it cannot infer the Supplier from a
standard parent field, and the multi-currency reference-details branch does not
return the JE total/outstanding. Payment Ledger itself does retain the correct
account-currency outstanding.

## Decision

- Account names are never production constants. Accounting services emit
  semantic account keys and Company Account Mapping resolves them.
- Retained-ownership commission and consignment recognition follows ADR 0008:
  704 deducts the partner amount, 702 is reclassified to 703 for the retained
  fee, and 685 holds the unreported partner liability. No third-party COGS is
  recognized.
- Settlement Report submit creates a debt JE that debits the corresponding
  unreported liability and credits a Supplier Payable party row.
- The Supplier row keeps standard JE Account reference fields empty. A custom
  parent link connects the debt JE to exactly one Settlement Report.
- The application creates Payment Entry explicitly through a dedicated adapter.
  It sets Supplier, payable account, payment-date exchange rate, one standard JE
  reference and one custom Settlement Report link.
- A server-side guard derives the report of every referenced debt JE and rejects
  a PE unless the unique set equals its own report link.
- Obligation-currency outstanding is the business source of truth. Standard
  Payment Ledger is reconciled against it; display values from generic helper
  methods are not treated as authoritative for multi-currency JE.
- Supplier billing currency must match the Supplier payable account currency.
- ERPNext system-generated Exchange Gain/Loss JE is retained for the base-value
  difference between provisional debt rate and each Payment Entry rate.
- Corrections store both `economic_date` and `posting_date`. If the economic
  date is closed, the adjustment posts on the first open date.
- Cancellation order is Payment Entry, adjustment/debt JE, Settlement Report,
  then recognition JE. Cancelling a PE must restore currency outstanding before
  the debt document is cancelled.

## Consequences

- Stage 1 needs Company Account Mapping rather than fixture account names.
- Stage 4 needs thin Payment Entry/Journal Entry hooks that call the shared
  accounting and report-binding services.
- Report status is calculated from obligation-currency allocations, not only
  base-currency GL totals.
- Payment Entry cancellation also cancels its system Exchange Gain/Loss JE; the
  report remains payable until the application recalculates its status.
- Backdated revisions require an open-period resolver and approval policy; they
  must not mutate historical GL merely because the economic date is historical.
