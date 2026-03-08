from __future__ import annotations

from ukrainian_integrations.ecommerce.providers.prom_ua.provider import PromUAProvider


def get_providers():
    return [
        PromUAProvider(),
    ]
