from __future__ import annotations

import json
from urllib.parse import quote

import requests

RZ_DELIVERY_API_BASE = "https://rz-delivery.rozetka.ua"
JSON_RESPONSE_LIMIT = 8 * 1024 * 1024
LABEL_RESPONSE_LIMIT = 24 * 1024 * 1024


class RZDeliveryAPIError(RuntimeError):
    """The provider returned an explicit application-level rejection."""


class RZDeliveryClient:
    def __init__(
        self,
        api_token: str | None = None,
        api_base: str = RZ_DELIVERY_API_BASE,
        content_language: str = "uk",
    ):
        self.api_token = (api_token or "").strip()
        self.api_base = (api_base or RZ_DELIVERY_API_BASE).rstrip("/")
        self.content_language = str(content_language or "uk").strip().lower()
        if self.content_language not in {"uk", "en", "ru"}:
            raise ValueError("Rozetka Delivery content language must be uk, en or ru")

    def _headers(self, *, require_auth: bool) -> dict[str, str]:
        if require_auth and not self.api_token:
            raise ValueError("Rozetka Delivery API token is required")
        headers = {
            "Accept": "application/json",
            "Content-Language": self.content_language,
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    @staticmethod
    def _read_bounded(response, maximum: int) -> bytes:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared_length = int(content_length)
            except (TypeError, ValueError):
                declared_length = 0
            if declared_length > maximum:
                raise ValueError("Rozetka Delivery response is too large")

        chunks: list[bytes] = []
        received = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            received += len(chunk)
            if received > maximum:
                raise ValueError("Rozetka Delivery response is too large")
            chunks.append(chunk)
        return b"".join(chunks)

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        params: dict | None = None,
        require_auth: bool = True,
        maximum_response_bytes: int = JSON_RESPONSE_LIMIT,
    ) -> dict:
        url = f"{self.api_base}/{path.lstrip('/')}"
        response = requests.request(
            method.upper(),
            url,
            headers=self._headers(require_auth=require_auth),
            params=params or {},
            json=payload if method.upper() not in {"GET", "HEAD"} else None,
            timeout=(10, 40),
            stream=True,
            allow_redirects=False,
        )
        try:
            raw = self._read_bounded(response, maximum_response_bytes)
        finally:
            response.close()

        if response.status_code >= 300:
            raise requests.HTTPError(
                f"Rozetka Delivery HTTP {response.status_code}",
                response=response,
            )
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Rozetka Delivery returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("Rozetka Delivery returned an unexpected JSON shape")

        status_code = data.get("statusCode")
        if status_code is not None and status_code != 0:
            raise RZDeliveryAPIError(
                f"Rozetka Delivery rejected the request with statusCode {status_code}"
            )
        return data

    def verify(self) -> dict:
        return self.request("api/auth/verify")

    def search_cities(self, query: str, *, carrier: str | None = None, limit: int = 50) -> dict:
        params: dict = {"name": query, "page": 1, "limit": limit}
        if carrier:
            params["carrier"] = carrier
        return self.request("api/city", params=params, require_auth=False)

    def search_departments(
        self,
        city_id: str,
        *,
        query: str | None = None,
        carrier: str | None = None,
        can_receive_tracks: bool | None = None,
        can_give_out_tracks: bool | None = None,
        limit: int = 50,
    ) -> dict:
        params: dict = {"city_id": city_id, "page": 1, "limit": limit}
        if query:
            params["name"] = query
        if carrier:
            params["carrier"] = carrier
        if can_receive_tracks is not None:
            params["can_receive_tracks"] = "true" if can_receive_tracks else "false"
        if can_give_out_tracks is not None:
            params["can_give_out_tracks"] = "true" if can_give_out_tracks else "false"
        return self.request("api/department", params=params, require_auth=False)

    def create_track(self, data: dict) -> dict:
        return self.request("api/track", method="POST", payload={"data": data})

    def get_track(self, track_id: str) -> dict:
        encoded = quote(str(track_id), safe="")
        return self.request(f"api/track/{encoded}")

    def get_statuses(self, track_ids: list[str]) -> dict:
        return self.request("api/track/status", params={"id": track_ids})

    def get_label(self, track_ids: list[str]) -> dict:
        return self.request(
            "api/track/label",
            params={"id": track_ids},
            maximum_response_bytes=LABEL_RESPONSE_LIMIT,
        )
