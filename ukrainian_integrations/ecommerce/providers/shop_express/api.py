from __future__ import annotations

import json
from collections.abc import Callable

import requests

MAX_JSON_RESPONSE_BYTES = 16 * 1024 * 1024


class ShopExpressAPIError(RuntimeError):
    pass


class ShopExpressClient:
    def __init__(
        self,
        *,
        base_url: str,
        login: str,
        password: str,
        token: str | None = None,
        token_callback: Callable[[str], None] | None = None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.login = (login or "").strip()
        self.password = password or ""
        self.token = (token or "").strip()
        self.token_callback = token_callback
        if not self.base_url:
            raise ValueError("Shop-Express API base URL is required")
        if not self.login or not self.password:
            raise ValueError("Shop-Express API login and password are required")

    def authenticate(self) -> dict:
        data = self._post("api/auth", {"login": self.login, "password": self.password})
        if data.get("status") != "OK":
            raise ShopExpressAPIError(_response_message(data, "Shop-Express authentication failed"))
        token = str((data.get("response") or {}).get("token") or "").strip()
        if not token:
            raise ShopExpressAPIError("Shop-Express authentication response contains no token")
        self.token = token
        if self.token_callback:
            self.token_callback(token)
        return {"status": "OK"}

    def request(self, path: str, payload: dict | None = None) -> dict:
        if not self.token:
            self.authenticate()
        data = self._authorized_post(path, payload or {})
        if data.get("status") == "UNAUTHORIZED":
            self.token = ""
            self.authenticate()
            data = self._authorized_post(path, payload or {})
        status = data.get("status")
        if status not in {"OK", "WARNING"}:
            raise ShopExpressAPIError(_response_message(data, f"Shop-Express rejected {path}"))
        return data

    def export_orders(self, **filters) -> dict:
        return self.request("api/orders/export", {key: value for key, value in filters.items() if value not in (None, "")})

    def export_users(self, **filters) -> dict:
        return self.request("api/users/export", {key: value for key, value in filters.items() if value not in (None, "")})

    def import_products(self, products: list[dict]) -> dict:
        return self.request("api/catalog/import", {"products": products})

    def update_residues(self, products: list[dict]) -> dict:
        return self.request("api/catalog/importResidues", {"products": products})

    def update_orders(self, orders: list[dict]) -> dict:
        return self.request("api/orders/update", {"orders": orders})

    def export_statuses(self) -> dict:
        return self.request("api/statuses/export")

    def _authorized_post(self, path: str, payload: dict) -> dict:
        body = dict(payload)
        body["token"] = self.token
        return self._post(path, body)

    def _post(self, path: str, payload: dict) -> dict:
        response = requests.post(
            f"{self.base_url}/{path.lstrip('/')}",
            headers={"Accept": "application/json", "Content-Type": "application/json; charset=utf-8"},
            json=payload,
            timeout=(10, 60),
            stream=True,
            allow_redirects=False,
        )
        try:
            raw = _read_bounded(response, MAX_JSON_RESPONSE_BYTES)
        finally:
            response.close()
        if response.status_code >= 300:
            raise requests.HTTPError(f"Shop-Express HTTP {response.status_code}", response=response)
        if not raw:
            raise ShopExpressAPIError("Shop-Express returned an empty response")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ShopExpressAPIError("Shop-Express returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ShopExpressAPIError("Shop-Express returned an unexpected JSON shape")
        return data


def _read_bounded(response, maximum: int) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared:
        try:
            if int(declared) > maximum:
                raise ShopExpressAPIError("Shop-Express response is too large")
        except ValueError:
            pass
    chunks = []
    received = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        received += len(chunk)
        if received > maximum:
            raise ShopExpressAPIError("Shop-Express response is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _response_message(data: dict, fallback: str) -> str:
    response = data.get("response")
    if isinstance(response, dict) and response.get("message"):
        return f"{fallback}: {str(response['message'])[:500]}"
    return fallback
