# PrivatBank POS production flow

```text
ERPNext → authenticated HTTPS gateway → terminal TCP endpoint
```

The ERP application never connects directly to a terminal. `PB POS Settings` defines one explicit gateway protocol (`legacy` or `v1`) and `PB POS Terminal` contains the allowlisted terminal IP/port.

## Safety guarantees

- Sales are limited to submitted UAH Sales Invoices and cannot exceed outstanding amount.
- Every sale, real test payment and refund requires an idempotency key.
- The request is recorded as `unknown` immediately before the gateway call.
- No automatic protocol fallback or financial retry is performed.
- One database lock serializes calls to a terminal within ERPNext.
- Explicit 4xx/provider declines become `failed`; timeout, 5xx, empty or malformed responses remain `unknown`.

An `unknown` result means that the card may have been charged. Check the physical receipt, gateway journal and acquirer report, then resolve the matching `UA Integration Operation`. Never create a new key merely to bypass reconciliation.

## Configuration

- `PB POS Settings.gateway_url`: HTTPS by default. For a controlled private-network HTTP gateway, set `pb_pos_allow_insecure_http=1` in `site_config.json`.
- `PB POS Settings.api_key`: Password field.
- `request_timeout_sec`: 5–180 seconds.
- `allow_test_operations`: keep off in production except during an approved, supervised acceptance window.
- Terminal records require a valid IPv4/IPv6 address and TCP port 1–65535.

Settings are authoritative when their DocType exists; a read/decryption error does not fall back to site configuration.

## Acceptance

1. Test connection on every active terminal.
2. Enable real test operations for a short supervised window.
3. Run a low-value payment with a unique key and verify ERP, gateway and physical receipt identifiers.
4. Run a refund against that receipt and verify the acquirer report.
5. Simulate gateway timeout after submission; verify the operation remains `unknown` and no retry occurs.
6. Disable real test operations again.
