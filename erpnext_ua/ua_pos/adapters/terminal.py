from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

import requests


@dataclass(frozen=True)
class TerminalResult:
	status: str
	rrn: str | None = None
	invoice_number: str | None = None
	auth_code: str | None = None
	card_mask: str | None = None
	merchant_id: str | None = None
	device_id: str | None = None
	payment_system_name: str | None = None
	acquirer_name: str | None = None
	amount: float | None = None
	currency: str = "UAH"
	receipt_text: str | None = None
	raw: dict[str, Any] = field(default_factory=dict)

	def as_dict(self) -> dict[str, Any]:
		return asdict(self)


class TerminalAdapter(ABC):
	@abstractmethod
	def identify(self, terminal: dict[str, Any]) -> dict[str, Any]: ...

	@abstractmethod
	def terminal_info(self, terminal: dict[str, Any]) -> dict[str, Any]: ...

	@abstractmethod
	def ping(self, terminal: dict[str, Any]) -> bool: ...

	@abstractmethod
	def sale(self, terminal: dict[str, Any], amount: float, operation_id: str) -> TerminalResult: ...

	@abstractmethod
	def refund(
		self, terminal: dict[str, Any], amount: float, operation_id: str, reference: str
	) -> TerminalResult: ...

	@abstractmethod
	def void(self, terminal: dict[str, Any], operation_id: str, reference: str) -> TerminalResult: ...

	@abstractmethod
	def status(self, terminal: dict[str, Any], operation_id: str) -> TerminalResult: ...


class PrivatPOSGatewayClient:
	"""HTTP-клієнт pb-pos-gateway з безпечними ретраями лише за operation_id."""

	def __init__(self, base_url: str, api_key: str, timeout: int = 20):
		self.base_url = (base_url or "").rstrip("/")
		self.api_key = (api_key or "").strip()
		self.timeout = int(timeout or 20)
		if not self.base_url or not self.api_key:
			raise ValueError("PB POS gateway URL and API key are required")

	def _request(self, method: str, path: str, *, json: dict | None = None) -> dict:
		headers = {
			"Authorization": f"Bearer {self.api_key}",
			"X-API-Key": self.api_key,
			"Content-Type": "application/json",
		}
		response = requests.request(
			method, f"{self.base_url}{path}", json=json, headers=headers, timeout=self.timeout
		)
		body = (response.text or "").strip()
		try:
			data = response.json() if body else {"ok": response.ok}
		except ValueError:
			data = {"ok": False, "error": True, "description": body or f"HTTP {response.status_code}"}
		if response.status_code >= 400:
			data.setdefault("error", True)
			data.setdefault("description", f"HTTP {response.status_code}")
		data["_http_status"] = response.status_code
		return data

	def operation(
		self,
		operation: str,
		terminal_ip: str,
		amount: float,
		operation_id: str,
		*,
		port: int = 2000,
		currency: str = "UAH",
		reference: str | None = None,
	) -> dict:
		payload = {
			"operation": operation,
			"terminal_ip": terminal_ip,
			"terminal_port": int(port or 2000),
			"amount": float(amount),
			"currency": currency,
			"operation_id": operation_id,
			"reference_operation_id": reference,
		}
		result = self._request("POST", "/v1/pos/operation", json=payload)
		# Legacy fallback is safe only when the modern endpoint is definitively absent.
		# A 5xx/unknown response may follow a successfully processed payment.
		if result.get("_http_status") != 404:
			return result

		legacy_path = {"sale": "/purchase", "refund": "/refund", "void": "/void"}.get(operation)
		if not legacy_path:
			return result
		params = {"amount": float(amount), "operation_id": operation_id}
		if reference:
			params["invoiceNumber"] = reference
		return self._request("POST", legacy_path, json={"terminal": terminal_ip, "params": params})

	def ping(self, terminal_ip: str, port: int = 2000) -> dict:
		return self._request(
			"POST", "/verify", json={"terminal": terminal_ip, "params": {"port": int(port)}}
		)

	def terminal_info(self, terminal_ip: str, port: int = 2000) -> dict:
		return self._request(
			"POST", "/terminalinfo", json={"terminal": terminal_ip, "params": {"port": int(port)}}
		)

	def identify(self, terminal_ip: str, port: int = 2000) -> dict:
		return self._request(
			"POST",
			"/identify",
			json={"terminal": terminal_ip, "params": {"msgType": "identify", "port": int(port)}},
		)

	def status(self, terminal_ip: str, operation_id: str, port: int = 2000) -> dict:
		return self._request(
			"POST",
			"/status",
			json={"terminal": terminal_ip, "operation_id": operation_id, "params": {"port": int(port)}},
		)


class PrivatPosAdapter(TerminalAdapter):
	def __init__(self, client: PrivatPOSGatewayClient):
		self.client = client

	@staticmethod
	def _result(raw: dict, *, amount: float | None = None) -> TerminalResult:
		payload = raw.get("params") if isinstance(raw.get("params"), dict) else raw
		code = str(payload.get("responseCode") or payload.get("response_code") or "")
		status = str(payload.get("status") or payload.get("result") or "").lower()
		if code == "0000" or status in {"ok", "success", "approved", "confirmed"}:
			status = "confirmed"
		elif status in {"cancelled", "canceled", "voided"}:
			status = "cancelled"
		elif status in {"declined", "rejected", "failed"} or (code and code != "0000"):
			status = "declined"
		elif raw.get("error") or status in {"timeout", "unknown"}:
			status = "unknown"
		else:
			status = "unknown"
		return TerminalResult(
			status=status,
			rrn=payload.get("rrn"),
			invoice_number=payload.get("invoice_number") or payload.get("invoiceNumber"),
			auth_code=payload.get("auth_code") or payload.get("authCode") or payload.get("approvalCode"),
			card_mask=payload.get("card_mask") or payload.get("cardMask") or payload.get("pan"),
			merchant_id=payload.get("merchant_id") or payload.get("merchantId") or payload.get("merchant"),
			device_id=payload.get("device_id") or payload.get("deviceId") or payload.get("terminalId"),
			payment_system_name=payload.get("payment_system_name") or payload.get("paymentSystem"),
			acquirer_name=payload.get("acquirer_name") or payload.get("bankAcquirer"),
			amount=amount,
			receipt_text=payload.get("receipt_text") or payload.get("receipt"),
			raw=raw,
		)

	def ping(self, terminal: dict[str, Any]) -> bool:
		raw = self.client.ping(terminal["ip"], terminal.get("port", 2000))
		description = str(raw.get("description") or "").lower()
		return not raw.get("error") or "log file is empty" in description

	def terminal_info(self, terminal: dict[str, Any]) -> dict[str, Any]:
		return self.client.terminal_info(terminal["ip"], terminal.get("port", 2000))

	def identify(self, terminal: dict[str, Any]) -> dict[str, Any]:
		return self.client.identify(terminal["ip"], terminal.get("port", 2000))

	def sale(self, terminal: dict[str, Any], amount: float, operation_id: str) -> TerminalResult:
		raw = self.client.operation(
			"sale", terminal["ip"], amount, operation_id, port=terminal.get("port", 2000)
		)
		return self._result(raw, amount=amount)

	def refund(
		self, terminal: dict[str, Any], amount: float, operation_id: str, reference: str
	) -> TerminalResult:
		raw = self.client.operation(
			"refund",
			terminal["ip"],
			amount,
			operation_id,
			port=terminal.get("port", 2000),
			reference=reference,
		)
		return self._result(raw, amount=amount)

	def void(self, terminal: dict[str, Any], operation_id: str, reference: str) -> TerminalResult:
		raw = self.client.operation(
			"void", terminal["ip"], 0, operation_id, port=terminal.get("port", 2000), reference=reference
		)
		return self._result(raw)

	def status(self, terminal: dict[str, Any], operation_id: str) -> TerminalResult:
		raw = self.client.status(terminal["ip"], operation_id, terminal.get("port", 2000))
		return self._result(raw)
