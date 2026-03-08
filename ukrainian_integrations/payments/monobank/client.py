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

    def create_invoice(self, amount_kopecks: int, ccy: int = 980, merchant_paym_info: dict | None = None, redirect_url: str | None = None, web_hook_url: str | None = None) -> dict:
        payload = {
            "amount": int(amount_kopecks),
            "ccy": int(ccy),
        }
        if merchant_paym_info:
            payload["merchantPaymInfo"] = merchant_paym_info
        if redirect_url:
            payload["redirectUrl"] = redirect_url
        if web_hook_url:
            payload["webHookUrl"] = web_hook_url

        r = requests.post(f"{MONOBANK_API}/api/merchant/invoice/create", json=payload, headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {}

    def get_invoice_status(self, invoice_id: str) -> dict:
        r = requests.get(f"{MONOBANK_API}/api/merchant/invoice/status?invoiceId={invoice_id}", headers=self._headers(), timeout=20)
        r.raise_for_status()
        return r.json() if (r.text or "").strip() else {}
