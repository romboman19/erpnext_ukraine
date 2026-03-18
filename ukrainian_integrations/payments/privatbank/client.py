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

    def statements(self, account: str, start_date: str, end_date: str, limit: int = 100, follow_id: str | None = None, group_id: str | None = None) -> dict:
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
            hdr = self._headers()
            if group_id:
                hdr["id"] = group_id
            r = requests.get(url, params=params, headers=hdr, timeout=45)
            txt = (r.text or "")[:800]
            attempts.append((f"GET {url}", r.status_code, txt))
            if r.ok:
                return r.json() if (r.text or "").strip() else {}

            # some company-mode endpoints reject id; retry once without id
            if group_id and r.status_code == 400 and 'Id in mode for companies should not be present' in (r.text or ''):
                hdr2 = self._headers()
                params2 = dict(params)
                params2.pop('id', None)
                r2 = requests.get(url, params=params2, headers=hdr2, timeout=45)
                attempts.append((f"GET {url} (no-id-retry)", r2.status_code, (r2.text or "")[:800]))
                if r2.ok:
                    return r2.json() if (r2.text or "").strip() else {}

        detail = " | ".join([f"{m} -> {c}: {t}" for (m, c, t) in attempts])
        raise requests.HTTPError(f"PrivatBank statements failed: {detail}")


    def settings(self, group_id: str | None = None) -> dict:
        attempts = []
        for url in self._endpoint_candidates('/statements/settings'):
            params = None
            hdr = self._headers()
            if group_id:
                hdr["id"] = group_id
            r = requests.get(url, params=params, headers=hdr, timeout=45)
            attempts.append((url, r.status_code, (r.text or '')[:800]))
            if r.ok:
                return r.json() if (r.text or "").strip() else {}
        detail = ' | '.join([f"{u} -> {c}: {t}" for u,c,t in attempts])
        raise requests.HTTPError(f"PrivatBank settings failed: {detail}")


    def balances(self, account: str | None, start_date: str, end_date: str | None = None, limit: int = 100, follow_id: str | None = None, group_id: str | None = None) -> dict:
        params = {
            "startDate": start_date,
            "limit": int(limit),
        }
        if end_date:
            params["endDate"] = end_date
        if account:
            params["acc"] = account
        if follow_id:
            params["followId"] = follow_id

        attempts = []
        for url in self._endpoint_candidates('/statements/balance'):
            hdr = self._headers()
            if group_id:
                hdr['id'] = group_id
            r = requests.get(url, params=params, headers=hdr, timeout=45)
            attempts.append((f"GET {url}", r.status_code, (r.text or "")[:800]))
            if r.ok:
                return r.json() if (r.text or "").strip() else {}

            if group_id and r.status_code == 400 and 'Id in mode for companies should not be present' in (r.text or ''):
                hdr2 = self._headers()
                params2 = dict(params)
                params2.pop('id', None)
                r2 = requests.get(url, params=params2, headers=hdr2, timeout=45)
                attempts.append((f"GET {url} (no-id-retry)", r2.status_code, (r2.text or "")[:800]))
                if r2.ok:
                    return r2.json() if (r2.text or "").strip() else {}

        detail = ' | '.join([f"{m} -> {c}: {t}" for (m,c,t) in attempts])
        raise requests.HTTPError(f"PrivatBank balances failed: {detail}")
