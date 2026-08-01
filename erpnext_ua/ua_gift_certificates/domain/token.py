from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
PREFIX = "GC1"
RANDOM_BYTES = 16


@dataclass(frozen=True)
class TokenMaterial:
    token: str
    normalized: str
    last4: str
    checksum: str


def normalize(value: str) -> str:
    return "".join((value or "").strip().upper().split())


def _checksum(payload: str) -> str:
    digest = hashlib.blake2s(payload.encode("ascii"), digest_size=3).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=")[:4]


def generate_token() -> TokenMaterial:
    random_part = base64.b32encode(secrets.token_bytes(RANDOM_BYTES)).decode("ascii").rstrip("=")
    body = f"{PREFIX}-{random_part}"
    checksum = _checksum(body)
    token = f"{body}-{checksum}"
    return TokenMaterial(token=token, normalized=token, last4=random_part[-4:], checksum=checksum)


def validate_token(value: str) -> str:
    normalized = normalize(value)
    parts = normalized.split("-")
    if len(parts) != 3 or parts[0] != PREFIX or len(parts[1]) != 26 or len(parts[2]) != 4:
        raise ValueError("invalid token format")
    if any(character not in ALPHABET for character in parts[1] + parts[2]):
        raise ValueError("invalid token alphabet")
    if not hmac.compare_digest(parts[2], _checksum(f"{parts[0]}-{parts[1]}")):
        raise ValueError("invalid token checksum")
    return normalized


def token_hash(value: str, secret: str) -> str:
    if not secret:
        raise ValueError("HMAC secret is unavailable")
    normalized = validate_token(value)
    return hmac.new(secret.encode("utf-8"), normalized.encode("ascii"), hashlib.sha256).hexdigest()


def masked(last4: str) -> str:
    return f"••••{last4}"
