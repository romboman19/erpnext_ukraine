from __future__ import annotations

import requests

UP_API_BASE_DEFAULT = "https://www.ukrposhta.ua/ecom/0.0.1"


class UkrPoshtaClient:
    def __init__(self, ecom_token: str, tracking_token: str | None = None, api_base: str = UP_API_BASE_DEFAULT):
        self.ecom_token = (ecom_token or "").strip()
        self.tracking_token = (tracking_token or "").strip() if tracking_token else ""
        self.api_base = (api_base or UP_API_BASE_DEFAULT).rstrip("/")
        if not self.ecom_token:
            raise ValueError("Ukrposhta ecom token is required")

    def _headers(self, token_kind: str = "ecom") -> dict:
        token = self.tracking_token if token_kind == "tracking" and self.tracking_token else self.ecom_token
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def request(
        self,
        path: str,
        method: str = "GET",
        payload: dict | None = None,
        params: dict | None = None,
        token_kind: str = "ecom",
    ) -> dict:
        url = f"{self.api_base}/{path.lstrip('/')}"
        is_body = method.upper() not in {"GET", "HEAD"}
        resp = requests.request(
            method.upper(),
            url,
            headers=self._headers(token_kind),
            params=params or {},
            json=(payload or {}) if is_body else None,
            timeout=40,
        )
        resp.raise_for_status()
        return resp.json() if (resp.text or "").strip() else {}

    def track(self, barcode: str) -> dict:
        return self.request(f"shipments/{barcode}", method="GET", token_kind="tracking")
