from __future__ import annotations

import requests

PROM_API_BASE = "https://my.prom.ua/api/v1"


class PromUAClient:
    def __init__(self, token: str, base_url: str = PROM_API_BASE):
        self.token = (token or "").strip()
        self.base_url = (base_url or PROM_API_BASE).rstrip("/")
        if not self.token:
            raise ValueError("PromUA token is required")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def list_orders(self, status: str | None = None, limit: int = 50, page: int = 1) -> dict:
        params = {"limit": int(limit), "page": int(page)}
        if status:
            params["status"] = status
        r = requests.get(f"{self.base_url}/orders/list", headers=self._headers(), params=params, timeout=30)
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {}

    def update_stock(self, rows: list[dict]) -> dict:
        payload = {"products": rows}
        r = requests.post(f"{self.base_url}/products/edit", headers=self._headers(), json=payload, timeout=40)
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {}
