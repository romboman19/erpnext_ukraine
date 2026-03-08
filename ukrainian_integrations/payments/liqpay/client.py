from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import requests

LIQPAY_API = "https://www.liqpay.ua/api"


class LiqPayClient:
    def __init__(self, public_key: str, private_key: str):
        self.public_key = (public_key or "").strip()
        self.private_key = (private_key or "").strip()
        if not self.public_key or not self.private_key:
            raise ValueError("LiqPay keys are required")

    def _encode_data(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return base64.b64encode(raw).decode("utf-8")

    def make_signature(self, data_b64: str) -> str:
        sign_str = f"{self.private_key}{data_b64}{self.private_key}".encode("utf-8")
        return base64.b64encode(hashlib.sha1(sign_str).digest()).decode("utf-8")

    def cnb_form_payload(self, payload: dict[str, Any]) -> dict[str, str]:
        data_b64 = self._encode_data(payload)
        return {"data": data_b64, "signature": self.make_signature(data_b64)}

    def api(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        signed = self.cnb_form_payload(payload)
        r = requests.post(f"{LIQPAY_API}/{path}", data=signed, timeout=30)
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {}
