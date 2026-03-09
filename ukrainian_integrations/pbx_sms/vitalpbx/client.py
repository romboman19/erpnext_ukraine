from __future__ import annotations

import requests


class VitalPBXClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 20, verify_ssl: bool = True, tenant: str | None = None):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout = int(timeout or 20)
        self.verify_ssl = bool(verify_ssl)
        self.tenant = (tenant or "").strip()
        if not self.base_url:
            raise ValueError("vitalpbx_base_url is required")
        if not self.api_key:
            raise ValueError("vitalpbx_api_key is required")

    def _headers(self, mode: str = "app-key") -> dict:
        if mode == "app-key":
            return {"app-key": self.api_key, "Content-Type": "application/json"}
        if mode == "x-api-key":
            return {"X-API-Key": self.api_key, "Content-Type": "application/json"}
        if mode == "raw-auth":
            return {"Authorization": self.api_key, "Content-Type": "application/json"}
        if mode == "apikey":
            return {"apikey": self.api_key, "Content-Type": "application/json"}
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def health(self) -> dict:
        params = {"tenant": self.tenant} if self.tenant else None
        for mode in ("app-key", "x-api-key", "raw-auth", "apikey", "bearer"):
            r = requests.get(f"{self.base_url}/api/ping", headers=self._headers(mode), params=params, timeout=self.timeout, verify=self.verify_ssl)
            if r.status_code != 401:
                r.raise_for_status()
                return r.json() if (r.text or "").strip() else {"ok": True}
        r.raise_for_status()
        return {"ok": False}

    def click_to_call(self, extension: str, destination: str) -> dict:
        payload = {"extension": str(extension), "destination": str(destination)}
        last = None
        params = {"tenant": self.tenant} if self.tenant else None
        for mode in ("app-key", "x-api-key", "raw-auth", "apikey", "bearer"):
            r = requests.post(f"{self.base_url}/api/click2call", headers=self._headers(mode), params=params, json=payload, timeout=self.timeout, verify=self.verify_ssl)
            last = r
            if r.status_code != 401:
                r.raise_for_status()
                return r.json() if (r.text or "").strip() else {"ok": True}
        last.raise_for_status()
        return {"ok": False}
