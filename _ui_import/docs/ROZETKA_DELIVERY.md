# Rozetka Delivery integration

The implementation follows the official [RZ-Delivery Swagger](https://rz-delivery.rozetka.ua/api/docs/). It creates department-to-department express waybills, tracks them and downloads provider-generated PDF labels.

## Supported workflow

- Static bearer token stored in a Frappe `Password` field.
- Sender profiles with optional Company isolation.
- Public directory lookup through `/api/city` and `/api/department`.
- Express-waybill creation through `POST /api/track` using the documented `{ "data": ... }` request envelope.
- Shipment weight in kilograms, dimensions in centimetres, places, delivery payer, insured value and optional COD.
- Status synchronization through `/api/track/status`, with `/api/track/{id}` as a read-only fallback for a missing batch row.
- Base64 PDF labels through `/api/track/label`, restricted to an authorized Sales Invoice or durable operation.

Only `dept-dept` is enabled. The official API also enumerates door delivery types, but enabling them without a separately validated address workflow would create an unsafe partial feature.

## Profile setup

1. In the Rozetka Delivery partner account, create a static token.
2. Create `RZ Delivery Sender Profile` in Desk.
3. Enter the official API base `https://rz-delivery.rozetka.ua`, token, sender name parts and Ukrainian phone in `380XXXXXXXXX` format.
4. Use `Select City and Department`; the sender lookup requests departments that can receive tracks.
5. Assign Company on multi-company sites and select one active default profile.
6. Save and run `Verify Token`.

Custom API hosts are rejected unless their hostname is explicitly listed in `rozetka_delivery_allowed_api_hosts`. Never allowlist an untrusted host because credentials would cross that network boundary.

## Sales Invoice flow

The invoice must be submitted, denominated in UAH and readable by the current Sales User. Choose `Rozetka Delivery → Create Shipment`, search the recipient city and a department able to give out tracks, enter parcel parameters and confirm.

`insurance_cost` must be positive. `cost=0` means no recipient collection; `cost>0` requests COD and cannot exceed the insured value. Rozetka Delivery may add its own recipient payment commission; the returned `payment_fee` is stored on the invoice.

Every creation requires a new `idempotency_key`. The app stores only a SHA-256 request fingerprint in `UA Integration Operation`, persists `unknown` before the provider mutation and never retries the mutation automatically. A 4xx application rejection becomes `failed`; timeouts, 408/429, 5xx, malformed success bodies and local persistence failures remain `unknown` for reconciliation.

## Reconciliation and labels

For `unknown`, search the partner cabinet by the Sales Invoice name (`visible_id`). If a track exists, record its ID and resolve the operation according to the production runbook; do not click Create again.

Label downloads validate authorization, base64, a 16 MiB size ceiling and the `%PDF-` signature. Status synchronization stores both provider status code and localized status name. Terminal codes stop scheduled polling.

## Go-live evidence

Complete the Rozetka Delivery section in `PROVIDER_ACCEPTANCE.md` using the organization's own account. Repository tests cannot validate contract activation, tariffs, actual department capabilities, credential permissions, COD settlement or provider-side account restrictions.
