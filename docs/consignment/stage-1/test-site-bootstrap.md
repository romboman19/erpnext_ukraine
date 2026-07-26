# Stage 1 test-site bootstrap

The command below is intentionally restricted to `postest.local` and Company
`POS Test Ukraine`. It creates reserved test masters and leaves
`CC Settings.enabled = 0`, so no transaction integration is activated.

```bash
bench --site postest.local execute \
  erpnext_ua.consignment_and_commission.setup.test_site.bootstrap_stage_1 \
  --kwargs '{"confirm_site":"postest.local","confirm_write":"SEED_STAGE_1_FOUNDATION"}'
```

Run the same command twice during rollout. The second result must have an empty
`created` list and must report all seven application-owned records in
`existing`. A conflicting record is never overwritten: the command raises and
rolls back instead.

Reserved records:

- `CC Test Main Location` using the three Gate 0C test warehouses;
- `CC Test Partner UAH` and ERPNext Supplier `CC Test Partner Supplier UAH`;
- one Account Mapping for `POS Test Ukraine`;
- Draft commission and consignment contracts, valid from `2026-07-13`;
- `CC Settings` defaults with both models available and the top-level feature
  gate disabled.
