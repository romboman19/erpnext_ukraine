from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Eligibility:
    allowed: bool
    reason_code: str
    earn_percent_override: Decimal | None = None
    extra_bonus_percent: Decimal | None = None
    max_redemption_percent_override: Decimal | None = None
