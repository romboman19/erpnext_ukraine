from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def select_sender_profile(
    profiles: Sequence[dict[str, Any]],
    *,
    carrier: str,
    requested: str | None = None,
    company: str | None = None,
) -> dict[str, Any]:
    """Select one profile, enforcing company ownership for business documents."""
    requested_name = str(requested or "").strip()
    company_name = str(company or "").strip()

    if requested_name:
        profile = next(
            (row for row in profiles if str(row.get("name") or "") == requested_name),
            None,
        )
        if not profile:
            raise ValueError(f"{carrier} sender profile not found or inactive: {requested_name}")
        _require_company(profile, company_name, carrier)
        return profile

    candidates = list(profiles)
    if company_name:
        candidates = [
            row for row in candidates if str(row.get("company") or "").strip() == company_name
        ]
        if not candidates:
            raise ValueError(f"No active {carrier} sender profile is configured for {company_name}")

    defaults = [row for row in candidates if bool(row.get("default"))]
    if len(defaults) == 1:
        return defaults[0]
    if len(defaults) > 1:
        raise ValueError(f"Multiple default {carrier} sender profiles match the document")
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"No active {carrier} sender profile is configured")
    raise ValueError(f"Select a {carrier} sender profile explicitly")


def _require_company(profile: dict[str, Any], company: str, carrier: str) -> None:
    if not company:
        return
    profile_company = str(profile.get("company") or "").strip()
    if profile_company != company:
        raise ValueError(f"{carrier} sender profile belongs to a different company")
