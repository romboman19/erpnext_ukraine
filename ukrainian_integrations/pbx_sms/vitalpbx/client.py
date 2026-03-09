from __future__ import annotations

import requests


class VitalPBXClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 20, verify_ssl: bool = True):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout = int(timeout or 20)
        self.verify_ssl = bool(verify_ssl)
        if not self.base_url:
            raise ValueError("vitalpbx_base_url is required")
        if not self.api_key:
            raise ValueError("vitalpbx_api_key is required")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def health(self) -> dict:
        r = requests.get(f"{self.base_url}/api/ping", headers=self._headers(), timeout=self.timeout, verify=self.verify_ssl)
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {"ok": True}

    def click_to_call(self, extension: str, destination: str) -> dict:
        payload = {"extension": str(extension), "destination": str(destination)}
        r = requests.post(f"{self.base_url}/api/click2call", headers=self._headers(), json=payload, timeout=self.timeout, verify=self.verify_ssl)
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {"ok": True}
