from __future__ import annotations

import requests


class PrivatPOSGatewayClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 20):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout = int(timeout or 20)
        if not self.base_url:
            raise ValueError("PB POS gateway URL is required")
        if not self.api_key:
            raise ValueError("PB POS gateway API key is required")

    def sale(self, terminal_ip: str, amount: float, operation_id: str, *, port: int = 2000, currency: str = "UAH") -> dict:
        payload = {
            "operation": "sale",
            "terminal_ip": terminal_ip,
            "terminal_port": int(port or 2000),
            "amount": float(amount),
            "currency": currency,
            "operation_id": operation_id,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        r = requests.post(f"{self.base_url}/v1/pos/operation", json=payload, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {"ok": False, "error": "empty_response"}

    def ping(self) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        r = requests.get(f"{self.base_url}/health", headers=headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {"ok": True}
