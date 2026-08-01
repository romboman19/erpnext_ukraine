# Testing

Run pure domain tests and Frappe integration tests:

```bash
bench --site <site> run-tests --app erpnext_ua --module erpnext_ua.ua_gift_certificates.tests.test_domain
bench --site <site> run-tests --app erpnext_ua --module erpnext_ua.ua_gift_certificates.tests.test_services
```

Required pilot additions: real PRRO test shift, terminal timeout recovery, two-session concurrent reserve, split multi-FOP invoices, restore-after-replacement and representative volume batch/reconciliation.
