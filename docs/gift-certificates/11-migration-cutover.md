# Migration and cutover

1. Backup database/private files and deploy with feature disabled.
2. Run migrate; verify roles, DocTypes, indexes and custom fields.
3. Configure accounts, Cost Center, compliance evidence, Network/Program and payment mappings.
4. Run readiness and acceptance suite; approve Stage 0 explicitly.
5. Pilot one desk/program, reconcile daily, then expand.

Legacy import must dry-run duplicate/entropy/funding/date checks. Plaintext source is private and must be purged after approved execution. Rollback disables flags; financial records are never deleted.
