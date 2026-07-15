# Provider acceptance checklist

Complete only the sections used by the deployment. Record date, operator, environment, provider account and evidence link/ticket. Never paste secrets into the evidence.

## Common gates

- [ ] TLS certificates and outbound DNS/egress are valid from every worker.
- [ ] Provider credentials have minimum required permissions and an owner/expiry.
- [ ] Host allowlists reject an unapproved test hostname.
- [ ] The same idempotency key returns the cached result or reconciliation error.
- [ ] Reusing a key with different data is rejected.
- [ ] A simulated timeout creates `unknown` and does not retry.
- [ ] Logs redact bearer tokens, API keys, signatures and query tokens.

## Nova Poshta

- [ ] Search settlement and warehouse with each active profile.
- [ ] Create one TTN, verify number/ref and download the operation-bound PDF label.
- [ ] Confirm another user without invoice permission cannot download the label.
- [ ] Confirm tracking scheduler updates the correct Sales Invoice.

## Ukrposhta

- [ ] Confirm 1.25 kg is submitted as 1250 grams.
- [ ] Create one address/client/shipment chain and verify barcode/status.
- [ ] Test `RETURN` and, if used, `PROCESS_AS_REFUSAL` in the provider sandbox.
- [ ] Confirm tracking works with the configured token set.

## Rozetka Delivery

- [ ] Create a static partner token and confirm `Verify Token` succeeds for every active sender profile.
- [ ] Select the sender city/department through the directory lookup and confirm the department accepts tracks.
- [ ] Create one controlled `dept-dept` shipment and reconcile `track_id`, shipping cost and estimated delivery date with the partner cabinet.
- [ ] Test `cost=0` and one controlled COD shipment; confirm the recipient amount and provider payment fee exactly.
- [ ] Download the PDF label from the Sales Invoice and confirm a user without invoice permission cannot download it.
- [ ] Simulate an HTTP 400 rejection and a timeout/HTTP 503; verify the operation becomes `failed` and `unknown`, respectively, with no automatic retry.
- [ ] Run manual and scheduled status synchronization through a terminal status and confirm terminal shipments leave the polling queue.
- [ ] Confirm a profile assigned to Company A cannot create a shipment for a Company B Sales Invoice.

## Monobank / PrivatBank

- [ ] Import a fixed historical range twice; the second run creates zero transactions.
- [ ] Reconcile provider count and signed amount total with ERP Bank Transactions.
- [ ] Test multi-page PrivatBank and multi-day Monobank ranges.
- [ ] Confirm Bank Account and Company mismatch is rejected.
- [ ] Confirm a provider/ERP Bank Account currency mismatch is rejected.

## LiqPay

- [ ] A newly decoded checkout payload has `version=7`, and its SHA3-256 signature is accepted by LiqPay sandbox.
- [ ] Valid signature/amount/currency/action callback succeeds once.
- [ ] Invalid signature, unknown order, changed amount, reused transaction ID and disallowed sandbox callback fail.
- [ ] Two concurrent successful callbacks create at most one Payment Entry.
- [ ] A paid invoice/partial prior reconciliation does not overbook automatically.
- [ ] A `reversed` callback after success is accepted, changes the operation to `unknown`, and raises a manual accounting-reconciliation signal without auto-cancelling entries.

## PB POS (`erpnext_ua.ua_pos`)

- [ ] Complete the supervised sequence in `docs/privat_pos_flow.md`.

## Customer identification

- [ ] Confirm unauthenticated users cannot call Desk identification APIs or retrieve customer PII.
- [ ] Confirm the same pending SMS request is reused and does not send a second message after an ambiguous timeout.
- [ ] Confirm Telegram rejects missing/wrong webhook secrets and accepts the configured secret.
- [ ] Verify customer data is returned only after SMS, Telegram or inbound-call verification succeeds.
- [ ] Simulate an ambiguous Telegram birthday send and confirm the `Unknown` log is not retried automatically.

## TurboSMS

- [ ] Send to one controlled number and record returned `message_id`.
- [ ] Provider HTTP 200 with non-zero `response_code` is recorded as failed.
- [ ] Timeout remains unknown and the same key is blocked from blind resend.

## VitalPBX

- [ ] Missing/wrong webhook key returns unauthorized.
- [ ] Ringing → answered → completed is monotonic; replaying ringing does not regress it.
- [ ] Sales users see only logs for their assigned extension.
- [ ] Click-to-call timeout remains unknown and is not retried.

## Prom.ua

- [ ] Paginate more than one page with `last_id` and import every order once.
- [ ] An order with no mapped Item creates neither Customer nor Sales Order.
- [ ] An order containing one mapped and one unmapped Item creates no partial Sales Order.
- [ ] Stock uses `/products/edit_by_external_id`, and every sent row appears in `processed_ids` with no errors.
- [ ] Only configured warehouses contribute stock, including the zero/not-available case.
