from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation


DIRECTION_SIGNS = {
	"Increase": 1,
	"Decrease": -1,
}


def direction_sign(direction: str) -> int:
	try:
		return DIRECTION_SIGNS[direction]
	except KeyError as exc:
		raise ValueError(f"Unsupported off-balance direction: {direction}") from exc


def validate_magnitudes(quantity: object = 0, amount: object = 0) -> tuple[Decimal, Decimal]:
	try:
		quantity_value = Decimal(str(quantity or 0))
		amount_value = Decimal(str(amount or 0))
	except (InvalidOperation, ValueError) as exc:
		raise ValueError("Quantity and amount must be finite numbers") from exc

	if not quantity_value.is_finite() or not amount_value.is_finite():
		raise ValueError("Quantity and amount must be finite numbers")
	if quantity_value < 0 or amount_value < 0:
		raise ValueError("Quantity and amount cannot be negative; use the direction field")
	if quantity_value == 0 and amount_value == 0:
		raise ValueError("Quantity or amount must be greater than zero")
	return quantity_value, amount_value


def signed_values(direction: str, quantity: object = 0, amount: object = 0) -> tuple[Decimal, Decimal]:
	quantity_value, amount_value = validate_magnitudes(quantity, amount)
	sign = direction_sign(direction)
	return quantity_value * sign, amount_value * sign


def validate_available_balance(
	*,
	available_quantity: object,
	available_amount: object,
	requested_quantity: object,
	requested_amount: object,
) -> None:
	available_quantity_value = Decimal(str(available_quantity or 0))
	available_amount_value = Decimal(str(available_amount or 0))
	requested_quantity_value, requested_amount_value = validate_magnitudes(requested_quantity, requested_amount)
	tolerance = Decimal("0.000001")

	if requested_quantity_value > available_quantity_value + tolerance:
		raise ValueError(
			f"Insufficient off-balance quantity: available {available_quantity_value}, "
			f"requested {requested_quantity_value}"
		)
	if requested_amount_value > available_amount_value + tolerance:
		raise ValueError(
			f"Insufficient off-balance amount: available {available_amount_value}, "
			f"requested {requested_amount_value}"
		)


def build_source_key(
	*,
	company: str,
	account: str,
	direction: str,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	reference_detail: str | None = None,
	external_reference_key: str | None = None,
) -> str | None:
	"""Build a stable retry key without putting business identifiers in the database index."""
	direction_sign(direction)
	external_reference_key = (external_reference_key or "").strip()
	reference_doctype = (reference_doctype or "").strip()
	reference_name = (reference_name or "").strip()
	reference_detail = (reference_detail or "").strip()

	if not external_reference_key and not (reference_doctype and reference_name):
		return None

	payload = {
		"version": 1,
		"company": (company or "").strip(),
		"account": (account or "").strip(),
		"direction": direction,
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
		"reference_detail": reference_detail,
		"external_reference_key": external_reference_key,
	}
	encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
	return hashlib.sha256(encoded).hexdigest()
