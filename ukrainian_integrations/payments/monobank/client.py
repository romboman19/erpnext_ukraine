from __future__ import annotations

import requests

MONOBANK_API = "https://api.monobank.ua"


class MonobankClient:
    def __init__(self, token: str):
        self.token = (token or "").strip()
        if not self.token:
            raise ValueError("Monobank token is required")

    def _headers(self) -> dict:
        return {"X-Token": self.token, "Content-Type": "application/json"}

    def statements(self, account: str, from_ts: int, to_ts: int) -> list[dict]:
        # Monobank personal API style endpoint
        # /personal/statement/{account}/{from}/{to}
        r = requests.get(
            f"{MONOBANK_API}/personal/statement/{account}/{int(from_ts)}/{int(to_ts)}",
            headers=self._headers(),
            timeout=30,
        )
        r.raise_for_status()
        data = r.json() if (r.text or "").strip() else []
        return data if isinstance(data, list) else []


    def client_info(self) -> dict:
        r = requests.get(
            f"{MONOBANK_API}/personal/client-info",
            headers=self._headers(),
            timeout=30,
        )
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {}
