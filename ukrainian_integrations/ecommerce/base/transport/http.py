from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from ukrainian_integrations.utils.operations import (
    load_response,
    mark_operation,
    require_new_or_return_success,
    reserve_operation,
)
from ukrainian_integrations.utils.validation import validate_allowed_host, validate_http_url

MAX_HTTP_RESPONSE_BYTES = 16 * 1024 * 1024
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class AmbiguousTransportError(RuntimeError):
    """The remote side effect may have happened and must not be retried blindly."""


@dataclass(frozen=True)
class HTTPRejectedError(RuntimeError):
    status_code: int
    response_excerpt: str = ""

    def __str__(self) -> str:
        return f"Remote HTTP request was rejected with status {self.status_code}"


class HTTPTransport:
    def __init__(
        self,
        *,
        base_url: str,
        allowlist_config_key: str,
        default_hosts: set[str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: tuple[int, int] = (10, 40),
        session: requests.Session | None = None,
    ):
        validate_http_url(base_url, "Ecommerce API Base URL")
        validate_allowed_host(
            base_url,
            "Ecommerce API Base URL",
            default_hosts=default_hosts or set(),
            config_key=allowlist_config_key,
        )
        self.base_url = str(base_url).rstrip("/")
        self.hostname = (urlparse(self.base_url).hostname or "").lower()
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.session = session or requests.Session()

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_payload: Any = None,
        idempotency_key: str | None = None,
        expected_statuses: set[int] | None = None,
    ) -> dict:
        verb = str(method or "GET").upper()
        if verb in MUTATING_METHODS and not str(idempotency_key or "").strip():
            raise ValueError("Mutating ecommerce HTTP requests require an idempotency key")
        request_headers = dict(self.headers)
        if idempotency_key:
            request_headers["Idempotency-Key"] = str(idempotency_key).strip()
        response = self._request(
            verb,
            path,
            params=params,
            json_payload=json_payload,
            headers=request_headers,
        )
        accepted = expected_statuses or set(range(200, 300))
        if response.status_code not in accepted:
            excerpt = _bounded_text(response.content, 1000)
            if verb in MUTATING_METHODS and response.status_code >= 500:
                raise AmbiguousTransportError(
                    f"Remote HTTP mutation returned server status {response.status_code}"
                )
            raise HTTPRejectedError(response.status_code, excerpt)
        if not response.content:
            return {}
        try:
            result = response.json()
        except ValueError as exc:
            if verb in MUTATING_METHODS:
                raise AmbiguousTransportError("Mutating HTTP response contains invalid JSON") from exc
            raise ValueError("HTTP response contains invalid JSON") from exc
        if not isinstance(result, dict):
            if verb in MUTATING_METHODS:
                raise AmbiguousTransportError("Mutating HTTP response has an unexpected shape")
            raise ValueError("HTTP response has an unexpected shape")
        return result

    def mutate_json(
        self,
        method: str,
        path: str,
        *,
        idempotency_key: str,
        integration: str,
        operation_type: str,
        json_payload: Any,
        reference_doctype: str | None = None,
        reference_name: str | None = None,
        expected_statuses: set[int] | None = None,
    ) -> dict:
        """Run one durable, immutable HTTP mutation with no blind retry."""
        request_payload = {
            "method": str(method).upper(),
            "path": str(path),
            "payload": json_payload,
        }
        reservation = reserve_operation(
            idempotency_key=idempotency_key,
            integration=integration,
            operation_type=operation_type,
            request_payload=request_payload,
            reference_doctype=reference_doctype,
            reference_name=reference_name,
        )
        cached = require_new_or_return_success(reservation)
        if cached is not None:
            return cached
        try:
            result = self.request_json(
                method,
                path,
                json_payload=json_payload,
                idempotency_key=idempotency_key,
                expected_statuses=expected_statuses,
            )
        except HTTPRejectedError as exc:
            mark_operation(
                reservation.doc,
                "failed",
                response_payload={"status_code": exc.status_code},
                error=str(exc),
            )
            raise
        except (AmbiguousTransportError, requests.Timeout, requests.ConnectionError) as exc:
            mark_operation(reservation.doc, "unknown", error=str(exc))
            raise AmbiguousTransportError(str(exc) or "Ambiguous ecommerce HTTP mutation") from exc
        except Exception as exc:
            mark_operation(reservation.doc, "unknown", error=str(exc))
            raise
        mark_operation(reservation.doc, "succeeded", response_payload=result)
        return load_response(reservation.doc)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None,
        json_payload: Any,
        headers: dict[str, str],
    ) -> requests.Response:
        endpoint = str(path or "").strip()
        if not endpoint.startswith("/") or endpoint.startswith("//"):
            raise ValueError("Ecommerce HTTP endpoint must be an absolute path on the configured host")
        url = f"{self.base_url}{endpoint}"
        if (urlparse(url).hostname or "").lower() != self.hostname:
            raise ValueError("Ecommerce HTTP endpoint changed the configured host")
        try:
            response = self.session.request(
                method,
                url,
                params=params,
                json=json_payload,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
                stream=True,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            if method in MUTATING_METHODS:
                raise AmbiguousTransportError("Ecommerce HTTP mutation timed out or disconnected") from exc
            raise
        content_length = response.headers.get("Content-Length")
        try:
            declared_length = int(content_length) if content_length else 0
        except (TypeError, ValueError):
            declared_length = 0
        if declared_length > MAX_HTTP_RESPONSE_BYTES:
            _raise_response_limit(method)
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_HTTP_RESPONSE_BYTES:
                _raise_response_limit(method)
            chunks.append(chunk)
        response._content = b"".join(chunks)
        return response


def _bounded_text(payload: bytes, limit: int) -> str:
    return bytes(payload or b"")[:limit].decode("utf-8", errors="replace")


def _raise_response_limit(method: str) -> None:
    if method in MUTATING_METHODS:
        raise AmbiguousTransportError("Mutating HTTP response is too large")
    raise ValueError("Ecommerce HTTP response is too large")
