from __future__ import annotations

import requests

PRIVATBANK_API_BASE = "https://acp.privatbank.ua/api/proxy"


class PrivatbankClient:
    def __init__(self, token: str, base_url: str = PRIVATBANK_API_BASE):
        self.token = (token or "").strip()
        self.base_url = (base_url or PRIVATBANK_API_BASE).rstrip("/")
        if not self.token:
            raise ValueError("PrivatBank token is required")

    def _headers(self) -> dict:
        return {
            "token": self.token,
            "User-Agent": "ERPNext-Ukrainian-Integrations/1.0",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def statements(self, account: str, start_date: str, end_date: str, limit: int = 1000, offset: int = 0) -> dict:
        payload = {
            "account": account,
            "startDate": start_date,
            "endDate": end_date,
            "pagination": {"limit": int(limit), "offset": int(offset)},
        }
        # Autoclient API v3 primary endpoint
        r = requests.post(
            f"{self.base_url}/statements/transactions",
            json=payload,
            headers=self._headers(),
            timeout=45,
        )
        # fallback to legacy endpoint if server does not support /transactions
        if r.status_code in (404, 405):
            r = requests.post(
                f"{self.base_url}/statements",
                json=payload,
                headers=self._headers(),
                timeout=45,
            )
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {}


    def settings(self) -> dict:
        r = requests.get(
            f"{self.base_url}/statements/settings",
            headers=self._headers(),
            timeout=45,
        )
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {}
