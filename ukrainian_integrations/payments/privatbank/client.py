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
            "Content-Type": "application/json;charset=utf-8",
            "Accept": "application/json",
        }

    def _endpoint_candidates(self, path: str) -> list[str]:
        base = self.base_url.rstrip("/")
        p = path if path.startswith("/") else f"/{path}"
        cands = [f"{base}{p}"]

        # if base is host root, try /api and /api/proxy variants
        if not base.endswith('/api') and not base.endswith('/api/proxy'):
            cands.append(f"{base}/api{p}")
            cands.append(f"{base}/api/proxy{p}")

        # if base is /api/proxy, also try /api variant
        if base.endswith('/api/proxy'):
            root = base[:-len('/api/proxy')]
            cands.append(f"{root}/api{p}")

        out = []
        for u in cands:
            if u not in out:
                out.append(u)
        return out

    def statements(self, account: str, start_date: str, end_date: str, limit: int = 100, follow_id: str | None = None) -> dict:
        # PrivatBank Autoclient v3 docs: GET /api/statements/transactions
        # params: acc, startDate=DD-MM-YYYY, endDate=DD-MM-YYYY, followId, limit
        params = {
            "acc": account,
            "startDate": start_date,
            "endDate": end_date,
            "limit": int(limit),
        }
        if follow_id:
            params["followId"] = follow_id

        attempts = []
        for url in self._endpoint_candidates('/statements/transactions') + self._endpoint_candidates('/statements'):
            r = requests.get(url, params=params, headers=self._headers(), timeout=45)
            attempts.append((f"GET {url}", r.status_code, (r.text or "")[:800]))
            if r.ok:
                return r.json() if (r.text or "").strip() else {}

        detail = " | ".join([f"{m} -> {c}: {t}" for (m, c, t) in attempts])
        raise requests.HTTPError(f"PrivatBank statements failed: {detail}")


    def settings(self) -> dict:
        r = requests.get(
            f"{self.base_url}/statements/settings",
            headers=self._headers(),
            timeout=45,
        )
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {}
