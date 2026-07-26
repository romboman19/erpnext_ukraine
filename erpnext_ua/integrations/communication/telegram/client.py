from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import requests

MAX_TELEGRAM_RESPONSE_BYTES = 1024 * 1024
TELEGRAM_CHAT_ID_PATTERN = re.compile(r"-?\d{1,20}")
TELEGRAM_TOKEN_PATTERN = re.compile(r"\d{5,20}:[A-Za-z0-9_-]{20,200}")
_ALLOWED_METHODS = frozenset({"sendDocument", "sendMessage"})


class TelegramAPIError(RuntimeError):
    """A Telegram failure classified by whether no side effect is certain."""

    def __init__(self, message: str, *, definite: bool):
        super().__init__(message)
        self.definite = definite


def is_valid_chat_id(value: str | int | None) -> bool:
    return bool(TELEGRAM_CHAT_ID_PATTERN.fullmatch(str(value or "").strip()))


def is_valid_bot_token(value: str | None) -> bool:
    return bool(TELEGRAM_TOKEN_PATTERN.fullmatch(str(value or "").strip()))


def _read_bounded(response) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > MAX_TELEGRAM_RESPONSE_BYTES:
                raise TelegramAPIError("Telegram API response is too large", definite=False)
        except ValueError:
            pass

    chunks: list[bytes] = []
    received = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        received += len(chunk)
        if received > MAX_TELEGRAM_RESPONSE_BYTES:
            raise TelegramAPIError("Telegram API response is too large", definite=False)
        chunks.append(chunk)
    return b"".join(chunks)


class TelegramClient:
    """Minimal Bot API client with a fixed host, bounded responses and no retries."""

    def __init__(self, token: str, *, post: Callable[..., Any] | None = None):
        self._token = str(token or "").strip()
        if not is_valid_bot_token(self._token):
            raise ValueError("Telegram bot token has an invalid format")
        self._post = post or requests.post

    def request(self, method: str, payload: dict, *, files: dict | None = None) -> dict:
        if method not in _ALLOWED_METHODS:
            raise ValueError("Unsupported Telegram API method")

        kwargs: dict[str, Any] = {
            "timeout": (10, 20),
            "allow_redirects": False,
            "stream": True,
        }
        if files:
            kwargs.update({"data": payload, "files": files})
        else:
            kwargs["json"] = payload

        try:
            response = self._post(
                f"https://api.telegram.org/bot{self._token}/{method}",
                **kwargs,
            )
            try:
                raw = _read_bounded(response)
            finally:
                response.close()
        except requests.RequestException:
            raise TelegramAPIError(
                "Telegram API request outcome is unknown",
                definite=False,
            ) from None

        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise TelegramAPIError(
                "Telegram API returned an invalid response",
                definite=False,
            ) from None
        if not isinstance(data, dict):
            raise TelegramAPIError(
                "Telegram API returned an unexpected response",
                definite=False,
            )
        if response.status_code >= 300 or data.get("ok") is not True:
            try:
                provider_code = int(data.get("error_code") or response.status_code)
            except (TypeError, ValueError):
                provider_code = response.status_code
            definite = 400 <= provider_code < 500 and provider_code not in {408, 429}
            raise TelegramAPIError(
                f"Telegram API rejected the request with HTTP {response.status_code}",
                definite=definite,
            )
        return data

    def send_message(self, *, chat_id: str, text: str, disable_web_page_preview: bool = True) -> dict:
        return self.request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "link_preview_options": {"is_disabled": bool(disable_web_page_preview)},
            },
        )

    def send_document(self, *, chat_id: str, content: bytes, filename: str, caption: str) -> dict:
        return self.request(
            "sendDocument",
            {"chat_id": chat_id, "caption": caption},
            files={"document": (filename, content, "application/pdf")},
        )
