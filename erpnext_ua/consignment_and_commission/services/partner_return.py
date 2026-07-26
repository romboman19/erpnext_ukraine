"""Pure validation and idempotent identity for unsold partner returns."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import Decimal
from typing import Any


class PartnerReturnError(ValueError):
    """Raised when a partner-return request violates its public contract."""


@dataclass(frozen=True, slots=True)
class PartnerReturnRequest:
    idempotency_key: str
    posting_date: date
    posting_time: Any
    source_lot: str
    qty: Decimal
    reason: str
    serial_numbers: tuple[str, ...] = ()


def canonical_posting_time(value: Any) -> str:
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, timedelta):
        total_microseconds = int(value.total_seconds() * 1_000_000)
        if total_microseconds < 0 or total_microseconds >= 86_400_000_000:
            raise PartnerReturnError("Partner return posting time must be within one day")
        hours, remainder = divmod(total_microseconds, 3_600_000_000)
        minutes, remainder = divmod(remainder, 60_000_000)
        seconds, microseconds = divmod(remainder, 1_000_000)
        result = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{result}.{microseconds:06d}" if microseconds else result
    text = str(value or "").strip()
    try:
        return time.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise PartnerReturnError("Partner return posting time must be HH:MM[:SS]") from exc


def validate_partner_return_request(request: PartnerReturnRequest) -> None:
    if (
        not request.idempotency_key
        or request.idempotency_key != request.idempotency_key.strip()
        or len(request.idempotency_key) > 140
    ):
        raise PartnerReturnError(
            "Partner return idempotency key must be 1-140 trimmed characters"
        )
    if not isinstance(request.posting_date, date):
        raise PartnerReturnError("Partner return posting date is required")
    canonical_posting_time(request.posting_time)
    if not request.source_lot or request.source_lot != request.source_lot.strip():
        raise PartnerReturnError("Partner return source lot is required and must be trimmed")
    qty = Decimal(str(request.qty))
    if not qty.is_finite() or qty <= 0:
        raise PartnerReturnError("Partner return quantity must be positive and finite")
    if not request.reason or request.reason != request.reason.strip():
        raise PartnerReturnError("Partner return reason is required and must be trimmed")
    serials = tuple(request.serial_numbers)
    if any(not value or value != value.strip() for value in serials):
        raise PartnerReturnError("Partner return Serial Nos must be non-empty and trimmed")
    if len(set(serials)) != len(serials):
        raise PartnerReturnError("Partner return Serial Nos must be unique")


def partner_return_fingerprint(request: PartnerReturnRequest) -> str:
    validate_partner_return_request(request)
    payload = {
        "posting_date": request.posting_date.isoformat(),
        "posting_time": canonical_posting_time(request.posting_time),
        "source_lot": request.source_lot,
        "qty": str(Decimal(str(request.qty)).normalize()),
        "reason": request.reason,
        "serial_numbers": sorted(request.serial_numbers),
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
