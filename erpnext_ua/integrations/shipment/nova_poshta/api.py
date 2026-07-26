from __future__ import annotations

import requests

NP_URL = "https://api.novaposhta.ua/v2.0/json/"


class NovaPoshtaClient:
    def __init__(self, api_key: str):
        self.api_key = (api_key or "").strip()
        if not self.api_key:
            raise ValueError("Nova Poshta API key is required")

    def call(
        self,
        model_name: str,
        called_method: str,
        method_properties: dict | None = None,
        api_key: str | None = None,
    ) -> dict:
        payload = {
            "apiKey": (api_key or self.api_key),
            "modelName": model_name,
            "calledMethod": called_method,
            "methodProperties": method_properties or {},
        }
        r = requests.post(NP_URL, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json() if r.text else {}
        if not data.get("success"):
            errors = data.get("errors") or data.get("warnings") or ["Nova Poshta API error"]
            raise RuntimeError("; ".join(map(str, errors)))
        return data

    def track(self, ttn: str) -> dict:
        out = self.call(
            "TrackingDocument",
            "getStatusDocuments",
            {"Documents": [{"DocumentNumber": ttn}]},
        )
        rows = out.get("data") or []
        return rows[0] if rows else {}
