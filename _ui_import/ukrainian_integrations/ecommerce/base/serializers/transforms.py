from __future__ import annotations

import html
import math
import re
from collections.abc import Callable
from typing import Any

_HTML_TAG = re.compile(r"<[^>]*>")
_CUSTOM_TRANSFORMS: dict[str, Callable[[Any], Any]] = {}


def register_custom_transform(method_path: str):
    """Register a trusted transform in code; database values are never imported."""
    name = str(method_path or "").strip()
    if not name or "." not in name:
        raise ValueError("A registered ecommerce transform requires a dotted name")

    def decorator(function: Callable[[Any], Any]):
        if name in _CUSTOM_TRANSFORMS and _CUSTOM_TRANSFORMS[name] is not function:
            raise ValueError(f"Ecommerce transform is already registered: {name}")
        _CUSTOM_TRANSFORMS[name] = function
        return function

    return decorator


def registered_custom_transforms() -> tuple[str, ...]:
    return tuple(sorted(_CUSTOM_TRANSFORMS))


def is_registered_custom_transform(method_path: str) -> bool:
    return str(method_path or "").strip() in _CUSTOM_TRANSFORMS


def apply_export_transform(value: Any, transform: str, custom_method: str = "") -> Any:
    selected = str(transform or "none").strip()
    if selected == "none":
        return value
    if selected == "html_strip":
        return html.unescape(_HTML_TAG.sub("", str(value or ""))).strip()
    if selected == "number_2dp":
        try:
            number = float(value or 0)
        except (TypeError, ValueError):
            raise ValueError("number_2dp received a non-numeric value") from None
        if not math.isfinite(number):
            raise ValueError("number_2dp received a non-finite value")
        return f"{number:.2f}"
    if selected == "custom-method-path":
        method_name = str(custom_method or "").strip()
        function = _CUSTOM_TRANSFORMS.get(method_name)
        if not function:
            raise ValueError(f"Ecommerce transform is not registered: {method_name or '?'}")
        return function(value)
    raise ValueError(f"Unsupported ecommerce field transform: {selected}")
