# Production readiness remediation

This document records the disposition of the production-readiness review against
`feat/ua-gift-certificates-v1`. It distinguishes implemented controls from
deployment evidence and business decisions.

## Implemented controls

### Ecommerce fiscalization

- Paid ecommerce Sales Invoices are fiscalized from the submitted Payment Entry,
  after the invoice becomes fully paid.
- The fiscal payment rows come from submitted Payment Entry allocations and their
  Mode of Payment configuration. Ecommerce invoices never fall back to cash.
- Each company must have one active `PRRO Cash Register` marked
  `Default for Ecommerce`. Missing configuration fails without selecting another
  FOP's register.
- A Payment Entry referenced by a fiscalized ecommerce invoice cannot be
  cancelled. The operator must create a return Sales Invoice and fiscal return.

This code path does not replace provider acceptance. Before enabling an online
channel, test the real PRRO, KEP, payment callback, uncertain-response recovery,
and customer receipt delivery end to end.

### Shipment sender ownership

- `NP Sender Profile` and `UP Sender Profile` belong to a required `Company`.
- One default profile is allowed per company.
- Sales Invoice actions list only profiles for the invoice company.
- Server-side shipment creation and scheduled tracking reject or log profiles
  that do not match the Sales Invoice company.
- Legacy unbound profiles must be assigned a company before they can create a
  shipment for a business document.

### GSF reservation expiry

The bounded `expire_due_allocations` sweeper is registered in `hourly_long`.
Clean-site CI imports and executes the scheduled service after two migrations.

### CI evidence

The clean-site workflow explicitly runs:

- Ukrainian accounting integration tests;
- the GSF expiry scheduled service;
- loyalty service integration tests;
- gift certificate service integration tests;
- all application integration tests.

The static workflow also verifies that GSF, loyalty, gift-certificate, Nova
Poshta, and Ukrposhta DocType definitions are included in the wheel.

### Tax parameters and calendar

- The 2026 group-1 seed is corrected from `302.80` to the official maximum
  `332.80`; provenance and a verification date are stored with every yearly set.
- Readiness requires a complete current-year set for groups 1–3.
- Groups 1–2 require a separately verified local rate on every FOP Profile, so
  Multi-FOP companies never share one community decision by accident.
- Calendar generation fails closed on missing fields, is POST-only, and requires
  an accounting-manager or system-manager role.
- Statutory and operational dates are stored separately. Weekend rules for
  declarations, quarterly tax, military levy, advance single tax, and ЄСВ are
  tested independently.
- Open generated deadlines are refreshed after migration; completed rows retain
  their historical operational date and receive missing provenance only.

## Remaining production gates

These items are not silently enabled by this remediation:

1. Ecommerce loyalty and gift-certificate redemption remain fail-closed. Their
   external API must identify the customer and securely transmit redemption
   credentials before the POS-only restriction can be removed.
2. Shipment creation from Sales Order or Delivery Note requires an explicit
   fulfilment workflow decision. Existing endpoints remain Sales Invoice based.
3. Automatic seller-FOP routing requires an approved policy for tax limits,
   stock ownership, returns, manual overrides, and audit responsibility.
4. Serial GSF and mixed GSF/consignment/commission sale and return flows run in
   clean-site CI. Batch, sustained load, deadlock, and failure-injection
   scenarios still require a production-shaped staging environment with
   scheduler and workers.
5. The legal acceptability and custody model of the external KEP signer requires
   owner and legal/compliance approval; code tests cannot grant that approval.

No feature gate should be enabled in production solely because CI is green.
Provider acceptance and staging evidence remain mandatory.
