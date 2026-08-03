from __future__ import annotations

from .errors import GiftCertificateError
from .money import ZERO, money


def status_after_balance(*, balance, redeemed_total, usage_policy: str) -> str:
    current = money(balance)
    redeemed = money(redeemed_total)
    if current < ZERO:
        raise GiftCertificateError("Certificate balance cannot be negative", "CERT_POSTING_INCOMPLETE")
    if current == ZERO:
        return "Fully Redeemed"
    if redeemed > ZERO:
        return "Partially Redeemed"
    return "Active"


def ensure_redeemable(status: str) -> None:
    errors = {
        "Issued": ("Certificate is not activated", "CERT_NOT_ACTIVATED"),
        "Draft": ("Certificate is not activated", "CERT_NOT_ACTIVATED"),
        "Fully Redeemed": ("Certificate is already used", "CERT_ALREADY_USED"),
        "Expired": ("Certificate has expired", "CERT_EXPIRED"),
        "Blocked": ("Certificate is blocked", "CERT_BLOCKED"),
        "Replaced": ("Certificate token was replaced", "CERT_REPLACED"),
        "Refunded": ("Certificate was refunded", "CERT_REFUNDED"),
        "Cancelled": ("Certificate is not valid", "CERT_NOT_FOUND"),
    }
    if status in errors:
        message, code = errors[status]
        raise GiftCertificateError(message, code)
    if status not in {"Active", "Partially Redeemed"}:
        raise GiftCertificateError("Certificate requires manual review", "CERT_MANUAL_REVIEW_REQUIRED")
