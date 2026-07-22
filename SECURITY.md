# Security policy

## Supported versions

Security fixes are provided for the latest 0.6.x release line on ERPNext/Frappe v16. Older commits should be upgraded before reporting a runtime issue.

## Reporting a vulnerability

Do not open a public issue containing credentials, personal data, webhook samples, payment identifiers or exploit details. Contact the maintainer at `it@hunter.rv.ua` with:

- affected commit/version and ERPNext version;
- minimal reproduction and impact;
- whether credentials or personal data may have been exposed;
- suggested embargo/contact details.

Rotate any potentially exposed provider credential immediately. Preserve sanitized logs and the matching `UA Integration Operation`; do not send raw site backups by email.

## Deployment assumptions

Production security depends on TLS, reverse-proxy request limits, restricted outbound egress, least-privilege Frappe roles, protected backups, patched ERPNext/Frappe images and provider-side credential controls. Query-string webhook secrets and insecure HTTP/SSL modes are opt-in exceptions and should remain disabled.
