from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ExchangeRateQuote:
    from_currency: str
    to_currency: str
    conversion_rate: Decimal
    rate_date: date
    source: str


@runtime_checkable
class ExchangeRateAdapter(Protocol):
    def get_rate(self, from_currency: str, to_currency: str, rate_date: date) -> ExchangeRateQuote: ...
