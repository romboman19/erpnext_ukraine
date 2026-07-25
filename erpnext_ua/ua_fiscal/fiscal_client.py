"""Надійний клієнт REST API Фіскального сервера ДПС (ЄВПЕЗ, 08.08.2025)."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
import requests

MAX_DPS_MESSAGE_BYTES = 200 * 1024
MAX_SIGNER_RESPONSE_BYTES = 4 * 1024 * 1024


class FiscalServerError(frappe.ValidationError):
	"""ДПС або signer відхилили коректно доставлений запит."""

	def __init__(self, message: str, *, error_code=None):
		super().__init__(message)
		self.error_code = error_code


class FiscalProtocolError(FiscalServerError):
	"""Відповідь сервера не відповідає задокументованому контракту."""


class FiscalTransportError(FiscalServerError):
	"""Запит не отримав певної відповіді; документ міг бути прийнятий ДПС."""

	def __init__(self, message: str, *, uncertain: bool = False):
		super().__init__(message)
		self.uncertain = uncertain


class FiscalClient:
	def __init__(self, settings=None, http=None):
		self.settings = settings or frappe.get_single("PRRO Settings")
		self.base_url = self.settings.get_fiscal_server_url()
		self.timeout = max(3, min(int(self.settings.request_timeout or 15), 120))
		self.http = http or requests.Session()
		self.http.headers.update({"User-Agent": "erpnext-ukraine-prro/0.4"})

	# --- signer ---

	def _signer_url(self, path: str) -> str:
		base = (self.settings.signservice_url or "").rstrip("/")
		if not base:
			frappe.throw("Не налаштовано URL сервісу підпису в PRRO Settings", FiscalServerError)
		return f"{base}{path}"

	def _signer_post(self, path: str, payload: dict) -> dict:
		try:
			response = self.http.post(
				self._signer_url(path),
				json=payload,
				headers={
					"x-api-key": self.settings.get_password("signservice_api_key") or "",
					"x-request-id": str(uuid.uuid4()),
				},
				timeout=self.timeout,
			)
		except (requests.Timeout, requests.ConnectionError) as exc:
			raise FiscalTransportError(f"Сервіс підпису недоступний: {exc}") from exc
		if len(response.content) > MAX_SIGNER_RESPONSE_BYTES:
			raise FiscalProtocolError("Відповідь сервісу підпису перевищує безпечний розмір")
		try:
			body = response.json()
		except ValueError as exc:
			raise FiscalProtocolError(
				f"Сервіс підпису повернув не-JSON відповідь (HTTP {response.status_code})"
			) from exc
		if response.status_code != 200:
			raise FiscalServerError(
				f"Сервіс підпису повернув HTTP {response.status_code}: {body.get('error') or 'невідома помилка'}"
			)
		return body

	def sign(self, data: bytes, kep_key: str, *, online: bool = True) -> bytes:
		"""Attached CAdES із signature-time-stamp для online і без TSP для offline."""
		key_doc = frappe.get_doc("UA KEP Key", kep_key)
		if key_doc.status != "Active":
			frappe.throw(f"Ключ КЕП {kep_key} має статус {key_doc.status}", FiscalServerError)
		if key_doc.valid_until and frappe.utils.getdate(key_doc.valid_until) < frappe.utils.getdate():
			frappe.throw(f"Строк дії КЕП {kep_key} минув", FiscalServerError)

		file_doc = frappe.get_doc("File", {"file_url": key_doc.key_file})
		key_content = file_doc.get_content()
		if isinstance(key_content, str):
			key_content = key_content.encode()
		payload = {
			"key": base64.b64encode(key_content).decode(),
			"password": key_doc.get_password("key_password"),
			"data": base64.b64encode(data).decode(),
			"detached": False,
			"tsp": "signature" if online else False,
		}
		body = self._signer_post("/api/sign", payload)
		try:
			return base64.b64decode(body["signature"], validate=True)
		except (KeyError, ValueError) as exc:
			raise FiscalProtocolError("Сервіс підпису не повернув коректний signature") from exc

	def unwrap(self, signed_data: bytes) -> bytes:
		"""Криптографічно перевіряє attached CMS і повертає вкладений документ."""
		body = self._signer_post(
			"/api/unwrap", {"data": base64.b64encode(signed_data).decode()}
		)
		if not body.get("cryptographically_verified") or not body.get("content"):
			raise FiscalProtocolError("Signer не підтвердив цілісність підписаної відповіді")
		try:
			return base64.b64decode(body["content"], validate=True)
		except ValueError as exc:
			raise FiscalProtocolError("Signer повернув некоректний content") from exc

	def signer_health(self) -> dict:
		try:
			response = self.http.get(self._signer_url("/health"), timeout=min(self.timeout, 5))
			response.raise_for_status()
			return response.json()
		except (requests.RequestException, ValueError) as exc:
			raise FiscalTransportError(f"Signer health check не пройдено: {exc}") from exc

	# --- DPS transport ---

	def _post_dps(self, path: str, *, data: bytes, content_type: str, uncertain: bool) -> requests.Response:
		try:
			response = self.http.post(
				f"{self.base_url}{path}",
				data=data,
				headers={"Content-Type": content_type, "Accept": "application/json, application/octet-stream"},
				timeout=self.timeout,
			)
		except (requests.Timeout, requests.ConnectionError) as exc:
			raise FiscalTransportError(
				f"Немає певної відповіді від фіскального сервера: {exc}", uncertain=uncertain
			) from exc
		if response.status_code >= 500:
			raise FiscalTransportError(
				f"Фіскальний сервер тимчасово недоступний: HTTP {response.status_code}",
				uncertain=uncertain,
			)
		if response.status_code not in (200, 204):
			raise FiscalServerError(
				f"Фіскальний сервер: HTTP {response.status_code} — {response.text[:500]}"
			)
		return response

	def send_document(self, signed_document: bytes) -> bytes:
		if not signed_document or len(signed_document) > MAX_DPS_MESSAGE_BYTES:
			raise FiscalProtocolError("Підписаний документ порожній або перевищує ліміт ДПС 200 KiB")
		return self._post_dps(
			"/doc", data=signed_document, content_type="application/octet-stream", uncertain=True
		).content

	def send_package(self, signed_package: bytes) -> bytes:
		if not signed_package or len(signed_package) > MAX_DPS_MESSAGE_BYTES:
			raise FiscalProtocolError("Офлайн-пакет порожній або перевищує ліміт ДПС 200 KiB")
		return self._post_dps(
			"/pck", data=signed_package, content_type="application/octet-stream", uncertain=True
		).content

	def command(self, payload: dict, signed_by: str | None = None) -> dict | None:
		payload = dict(payload)
		payload.setdefault("UID", str(uuid.uuid4()))
		try:
			tz = ZoneInfo(frappe.utils.get_system_timezone())
		except (ZoneInfoNotFoundError, AttributeError):
			timestamp = datetime.now().astimezone()
		else:
			timestamp = datetime.now(tz)
		# За протоколом ДПС Timestamp — ISO 8601 із часовим зміщенням, а
		# Timeout задається в мілісекундах. Наївний datetime тут небезпечний:
		# сервер може одразу скасувати команду через іншу локальну зону.
		payload.setdefault("Timestamp", timestamp.isoformat(timespec="seconds"))
		payload.setdefault("Timeout", self.timeout * 1000)
		raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
		if signed_by:
			raw = self.sign(raw, signed_by, online=True)
			content_type = "application/octet-stream"
		else:
			content_type = "application/json; charset=UTF-8"
		response = self._post_dps(
			"/cmd?resultAsJson=true", data=raw, content_type=content_type, uncertain=False
		)
		if response.status_code == 204 or not response.content:
			return None
		try:
			body = response.json()
		except ValueError as exc:
			raise FiscalProtocolError(f"Команда ДПС повернула не-JSON: {response.text[:300]}") from exc
		if body.get("ErrorCode") not in (None, 0, "0"):
			raise FiscalServerError(
				f"ДПС ErrorCode={body.get('ErrorCode')}: {body.get('ErrorMessage') or 'невідома помилка'}",
				error_code=body.get("ErrorCode"),
			)
		return body

	# --- commands ---

	def server_state(self) -> dict:
		return self.command({"Command": "ServerState"}) or {}

	def schemas(self) -> dict:
		return self.command({"Command": "Schemas"}) or {}

	def objects(self, kep_key: str) -> dict | None:
		return self.command({"Command": "Objects"}, signed_by=kep_key)

	def operators(self, kep_key: str) -> dict | None:
		return self.command({"Command": "Operators"}, signed_by=kep_key)

	def registrar_state(self, fiscal_number: str, kep_key: str, **extra) -> dict | None:
		return self.command(
			{"Command": "TransactionsRegistrarState", "NumFiscal": fiscal_number, **extra},
			signed_by=kep_key,
		)

	def last_shift_totals(self, fiscal_number: str, kep_key: str) -> dict | None:
		return self.command(
			{"Command": "LastShiftTotals", "NumFiscal": fiscal_number}, signed_by=kep_key
		)

	def document_info_by_local_number(
		self, fiscal_number: str, local_number: int, kep_key: str
	) -> dict | None:
		try:
			return self.command(
				{
					"Command": "DocumentInfoByLocalNum",
					"NumFiscal": fiscal_number,
					"NumLocal": int(local_number),
				},
				signed_by=kep_key,
			)
		except FiscalServerError as exc:
			# Для цієї команди ДПС повертає ErrorCode=13, коли документа з
			# указаним локальним номером не існує. Це очікувана негативна
			# відповідь під час recovery, а не збій фіскального сервера.
			if str(exc.error_code) == "13":
				return None
			raise

	def device_register(self, fiscal_number: str, device_id: str, kep_key: str, forced=False) -> dict:
		return self.command(
			{
				"Command": "DeviceRegister",
				"NumFiscal": fiscal_number,
				"DeviceId": device_id,
				"Forced": bool(forced),
			},
			signed_by=kep_key,
		) or {}


@frappe.whitelist()
def check_server_state():
	client = FiscalClient()
	return {"dps": client.server_state(), "signer": client.signer_health()}
