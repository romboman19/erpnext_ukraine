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
        headers = {
            "Content-Type": "application/json",
            "app-key": self.api_key,
            "tenant": self.tenant or "VitalPBX",
        }
        # best-effort probe
        r = requests.get(f"{self.base_url}/api/v2/core/click_to_call", headers=headers, timeout=self.timeout, verify=self.verify_ssl)
        if r.status_code in (200, 204, 400, 405):
            return {"ok": True, "status_code": r.status_code}
        r.raise_for_status()
        return {"ok": True, "status_code": r.status_code}

    def click_to_call(self, extension: str, destination: str) -> dict:
        payload = {
            "caller": str(extension),
            "callee": str(destination),
            "cos_id": 1,
            "cid_name": str(destination),
            "cid_number": str(destination),
        }
        headers = {
            "Content-Type": "application/json",
            "app-key": self.api_key,
            "tenant": self.tenant or "VitalPBX",
        }
        r = requests.post(
            f"{self.base_url}/api/v2/core/click_to_call",
            headers=headers,
            json=payload,
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {}


    def dialer_call(
        self,
        number: str,
        cos_id: int,
        destination_category_id: int,
        destination_id: int,
        cid_number: str | None = None,
        cid_name: str | None = None,
        timeout: int | None = None,
    ) -> dict:
        payload = {
            "number": str(number),
            "cos_id": int(cos_id),
            "destination_category_id": int(destination_category_id),
            "destination_id": int(destination_id),
        }
        if cid_number:
            payload["cid_number"] = str(cid_number)
        if cid_name:
            payload["cid_name"] = str(cid_name)
        if timeout is not None:
            payload["timeout"] = int(timeout)

        headers = {
            "Content-Type": "application/json",
            "app-key": self.api_key,
            "tenant": self.tenant or "VitalPBX",
        }
        r = requests.post(
            f"{self.base_url}/api/v2/core/dialer_call",
            headers=headers,
            json=payload,
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {}
