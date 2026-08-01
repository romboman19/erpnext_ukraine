# Data model

Authority: `UA Gift Certificate Ledger Entry` plus active reservations. Certificate balances are rebuildable caches. Redemption and Return Allocation preserve per-item provenance. Sale, Replacement, Settlement Entry, Tax Event, Configuration Audit and Print Grant provide immutable lineage.

All economic operations carry an idempotency key. Token lookup uses HMAC; encrypted token is a Password field and never a list/report field. Public serial is not secret.
