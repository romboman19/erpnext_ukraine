"""Pure request contract for purchasing third-party stock into OWN inventory."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from .partner_return import canonical_posting_time


class OwnershipConversionError(ValueError):
    """Raised when a conversion request violates its public contract."""


@dataclass(frozen=True, slots=True)
class OwnershipConversionRequest:
    idempotency_key: str
    posting_date: date
    posting_time: Any
    source_lot: str
    qty: Decimal
    source_method: str
    unit_cost: Decimal
    currency: str
    exchange_rate: Decimal
    reason: str
    due_date: date | None = None
    supplier_invoice_no: str = ""
    supplier_invoice_date: date | None = None
    serial_numbers: tuple[str, ...] = ()
    target_batch_no: str = ""


def validate_ownership_conversion_request(request: OwnershipConversionRequest) -> None:
    if (
        not request.idempotency_key
        or request.idempotency_key != request.idempotency_key.strip()
        or len(request.idempotency_key) > 140
    ):
        raise OwnershipConversionError(
            "Ownership conversion idempotency key must be 1-140 trimmed characters"
        )
    if not isinstance(request.posting_date, date):
        raise OwnershipConversionError("Ownership conversion posting date is required")
    try:
        canonical_posting_time(request.posting_time)
    except ValueError as exc:
        raise OwnershipConversionError(str(exc).replace("Partner return", "Conversion")) from exc
    if not request.source_lot or request.source_lot != request.source_lot.strip():
        raise OwnershipConversionError("Ownership conversion source lot is required and trimmed")
    if request.source_method not in {"BUYOUT", "DEFERRED_PURCHASE"}:
        raise OwnershipConversionError("Conversion purchase method is not supported")
    qty = Decimal(str(request.qty))
    unit_cost = Decimal(str(request.unit_cost))
    exchange_rate = Decimal(str(request.exchange_rate))
    if not qty.is_finite() or qty <= 0:
        raise OwnershipConversionError("Conversion quantity must be positive and finite")
    if not unit_cost.is_finite() or unit_cost <= 0:
        raise OwnershipConversionError("Conversion unit cost must be positive and finite")
    if not exchange_rate.is_finite() or exchange_rate <= 0:
        raise OwnershipConversionError("Conversion exchange rate must be positive and finite")
    if not request.currency or request.currency != request.currency.strip():
        raise OwnershipConversionError("Conversion currency is required and trimmed")
    if not request.reason or request.reason != request.reason.strip():
        raise OwnershipConversionError("Conversion reason is required and trimmed")
    if request.source_method == "BUYOUT" and request.due_date:
        raise OwnershipConversionError("BUYOUT conversion cannot set a due date")
    if request.source_method == "DEFERRED_PURCHASE" and not request.due_date:
        raise OwnershipConversionError("DEFERRED_PURCHASE conversion requires a due date")
    serials = tuple(request.serial_numbers)
    if any(not value or value != value.strip() for value in serials):
        raise OwnershipConversionError("Conversion Serial Nos must be non-empty and trimmed")
    if len(set(serials)) != len(serials):
        raise OwnershipConversionError("Conversion Serial Nos must be unique")
    if request.target_batch_no and request.target_batch_no != request.target_batch_no.strip():
        raise OwnershipConversionError("Target Batch must be trimmed")


def ownership_conversion_fingerprint(request: OwnershipConversionRequest) -> str:
    validate_ownership_conversion_request(request)

    def decimal(value: Decimal) -> str:
        return str(Decimal(str(value)).normalize())

    payload = {
        "posting_date": request.posting_date.isoformat(),
        "posting_time": canonical_posting_time(request.posting_time),
        "source_lot": request.source_lot,
        "qty": decimal(request.qty),
        "source_method": request.source_method,
        "unit_cost": decimal(request.unit_cost),
        "currency": request.currency,
        "exchange_rate": decimal(request.exchange_rate),
        "reason": request.reason,
        "due_date": request.due_date.isoformat() if request.due_date else None,
        "supplier_invoice_no": request.supplier_invoice_no,
        "supplier_invoice_date": (
            request.supplier_invoice_date.isoformat() if request.supplier_invoice_date else None
        ),
        "serial_numbers": sorted(request.serial_numbers),
        "target_batch_no": request.target_batch_no,
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
