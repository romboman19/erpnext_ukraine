from __future__ import annotations

from urllib.parse import quote

import requests

UP_API_BASE_DEFAULT = "https://www.ukrposhta.ua/ecom/0.0.1"


class UkrPoshtaClient:
    def __init__(
        self,
        ecom_token: str,
        tracking_token: str | None = None,
        counterparty_token: str | None = None,
        api_base: str = UP_API_BASE_DEFAULT,
    ):
        self.ecom_token = (ecom_token or "").strip()
        self.tracking_token = (tracking_token or "").strip() if tracking_token else ""
        self.counterparty_token = (counterparty_token or "").strip() if counterparty_token else ""
        self.api_base = (api_base or UP_API_BASE_DEFAULT).rstrip("/")
        if not self.ecom_token:
            raise ValueError("Ukrposhta ecom token is required")

    def _headers(self, auth_mode: str = "ecom") -> dict:
        selected_credential = (
            self.tracking_token if auth_mode == "tracking" and self.tracking_token else self.ecom_token
        )
        return {"Authorization": f"Bearer {selected_credential}", "Content-Type": "application/json"}

    def request(
        self,
        path: str,
        method: str = "GET",
        payload: dict | None = None,
        params: dict | None = None,
        auth_mode: str = "ecom",
    ) -> dict:
        url = f"{self.api_base}/{path.lstrip('/')}"
        is_body = method.upper() not in {"GET", "HEAD"}
        resp = requests.request(
            method.upper(),
            url,
            headers=self._headers(auth_mode),
            params=params or {},
            json=(payload or {}) if is_body else None,
            timeout=40,
        )
        if resp.status_code >= 300:
            try:
                err_body = resp.json()
            except Exception:
                err_body = resp.text[:500] if resp.text else ""
            raise requests.HTTPError(
                f"Ukrposhta HTTP {resp.status_code}: {err_body}",
                response=resp,
            )
        return resp.json() if (resp.text or "").strip() else {}

    # ── Address ──────────────────────────────────────────────────────────────

    def create_address(self, payload: dict) -> dict:
        """POST /addresses → returns address dict with 'id'."""
        return self.request("addresses", method="POST", payload=payload, auth_mode="ecom")

    # ── Client ───────────────────────────────────────────────────────────────

    def create_client(self, payload: dict) -> dict:
        """POST /clients?token=<counterparty> → returns client dict with 'uuid'."""
        params = {}
        if self.counterparty_token:
            params["token"] = self.counterparty_token
        return self.request("clients", method="POST", payload=payload, params=params, auth_mode="ecom")

    # ── Shipment ─────────────────────────────────────────────────────────────

    def create_shipment(self, payload: dict) -> dict:
        """POST /shipments?token=<counterparty> → returns shipment dict."""
        params = {}
        if self.counterparty_token:
            params["token"] = self.counterparty_token
        return self.request("shipments", method="POST", payload=payload, params=params, auth_mode="ecom")

    # ── Tracking ─────────────────────────────────────────────────────────────

    def track(self, barcode: str) -> dict:
        """GET /shipments/barcode/{barcode}?token=<tracking>"""
        params = {}
        if self.tracking_token:
            params["token"] = self.tracking_token
        return self.request(
            f"shipments/barcode/{quote(str(barcode), safe='')}",
            method="GET",
            params=params,
            auth_mode="tracking",
        )

    # ── Label ────────────────────────────────────────────────────────────────

    def get_label(self, shipment_id: str, form_type: str = "label") -> dict:
        """GET /shipments/{id}/{form_type}?token=<counterparty>"""
        params = {}
        if self.counterparty_token:
            params["token"] = self.counterparty_token
        return self.request(
            f"shipments/{quote(str(shipment_id), safe='')}/{quote(str(form_type), safe='')}",
            method="GET",
            params=params,
            auth_mode="ecom",
        )
