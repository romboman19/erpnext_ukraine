"""Pure identity and amount validation for settlement payments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation


class SettlementPaymentError(ValueError):
    """Raised when a controlled settlement payment is invalid."""


@dataclass(frozen=True, slots=True)
class SettlementPaymentRequest:
    idempotency_key: str
    settlement_report: str
    bank_account: str
    amount: Decimal
    posting_date: date
    reference_no: str
    exchange_rate: Decimal = Decimal("1")


def validate_payment_request(request: SettlementPaymentRequest) -> None:
    key = request.idempotency_key
    if not key or key != key.strip() or len(key) > 140:
        raise SettlementPaymentError(
            "Idempotency key must be 1-140 characters without edge whitespace"
        )
    for label, value in (
        ("Settlement Report", request.settlement_report),
        ("Bank Account", request.bank_account),
        ("Reference No", request.reference_no),
    ):
        if not value or value != value.strip():
            raise SettlementPaymentError(f"{label} is required without edge whitespace")
    try:
        amount = Decimal(str(request.amount))
        rate = Decimal(str(request.exchange_rate))
    except (InvalidOperation, ValueError) as exc:
        raise SettlementPaymentError("Payment amount and exchange rate must be decimal") from exc
    if not amount.is_finite() or not rate.is_finite() or amount <= 0 or rate <= 0:
        raise SettlementPaymentError("Payment amount and exchange rate must be positive and finite")


def payment_fingerprint(request: SettlementPaymentRequest) -> str:
    validate_payment_request(request)
    payload = {
        "settlement_report": request.settlement_report,
        "bank_account": request.bank_account,
        "amount": str(Decimal(str(request.amount)).normalize()),
        "posting_date": request.posting_date.isoformat(),
        "reference_no": request.reference_no,
        "exchange_rate": str(Decimal(str(request.exchange_rate)).normalize()),
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
