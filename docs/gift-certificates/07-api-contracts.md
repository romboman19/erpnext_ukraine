# API contracts

POS methods under `erpnext_ua.ua_gift_certificates.api.pos` quote/reserve/release redemption and quote/add sale rows. Admin methods issue, batch-generate and reconcile. All money is serialized as decimal strings at public boundaries; the server ignores client funding/accounting components.

Stable errors use `CERT_*` codes. Normal status responses are masked. Full token exists only in immediate issue response for authorized back office or one-time protected print payload.
