# Permissions and security

Cashiers cannot list the register or read ciphertext. Ledger/audit/print grants are service-only append-only records. Adjustment and replacement enforce separation of duties. Token failures log only a hash prefix.

Print responses use `Cache-Control: no-store`; token is converted to a barcode and removed from the response structure. No token is stored in POS Print Job payload. Duplicate printing needs an authorized role; one-time programs require replacement.
