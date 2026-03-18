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
        attempts = []

        # 1) Autoclient API v3 POST
        r = requests.post(
            f"{self.base_url}/statements/transactions",
            json=payload,
            headers=self._headers(),
            timeout=45,
        )
        attempts.append(("POST /statements/transactions", r.status_code, (r.text or "")[:800]))
        if r.ok:
            return r.json() if (r.text or "").strip() else {}

        # 2) Legacy POST
        r2 = requests.post(
            f"{self.base_url}/statements",
            json=payload,
            headers=self._headers(),
            timeout=45,
        )
        attempts.append(("POST /statements", r2.status_code, (r2.text or "")[:800]))
        if r2.ok:
            return r2.json() if (r2.text or "").strip() else {}

        # 3) GET variant (some gateways enforce query params)
        params = {
            "account": account,
            "startDate": start_date,
            "endDate": end_date,
            "limit": int(limit),
            "offset": int(offset),
        }
        r3 = requests.get(
            f"{self.base_url}/statements/transactions",
            params=params,
            headers=self._headers(),
            timeout=45,
        )
        attempts.append(("GET /statements/transactions", r3.status_code, (r3.text or "")[:800]))
        if r3.ok:
            return r3.json() if (r3.text or "").strip() else {}

        detail = " | ".join([f"{m} -> {c}: {t}" for (m,c,t) in attempts])
        raise requests.HTTPError(f"PrivatBank statements failed: {detail}", response=r3)


    def settings(self) -> dict:
        r = requests.get(
            f"{self.base_url}/statements/settings",
            headers=self._headers(),
            timeout=45,
        )
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {}
