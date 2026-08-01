# Reconciliation runbook

Daily: run certificate cache reconciliation, leaked reservation check, sale activation/payment evidence, ledger component equality, GL liability comparison, return over-restore check, settlement pair check and Fiscal Pending queue.

Safe repair may rebuild only cached balances/links from authority. Missing financial events require a controlled reversal/adjustment; never edit ledger rows. Escalate any negative balance, orphan allocation, unmatched settlement or unknown external payment.
