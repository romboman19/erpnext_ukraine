from decimal import Decimal

ZERO = Decimal("0.00")
CURRENCY = "UAH"
WRITE_FLAG = "ua_gift_certificate_service"
ACTIVE_CERTIFICATE_STATUSES = {"Active", "Partially Redeemed", "Fully Redeemed"}
ACTIVE_RESERVATION_STATUSES = {"Active", "Consuming"}
TERMINAL_CERTIFICATE_STATUSES = {"Cancelled", "Refunded", "Replaced"}
