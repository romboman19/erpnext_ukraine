from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any


class AbstractEcommerceChannel(ABC):
    """Provider contract expressed as entity × direction operations.

    A provider implements only the capabilities it actually supports. Callers
    must treat ``NotImplementedError`` as an unsupported route and keep it out
    of Desk actions and scheduler dispatch.
    """

    settings_doctype: str

    def __init__(self, settings: Any):
        self.settings = settings

    @property
    def channel_name(self) -> str:
        return str(self.settings.name)

    @property
    def channel_code(self) -> str:
        return f"{self.settings_doctype}:{self.channel_name}"

    def export_products(self, items: Iterable[Any]) -> dict:
        raise NotImplementedError

    def export_prices(self, items: Iterable[Any]) -> dict:
        raise NotImplementedError

    def export_stock(self, items: Iterable[Any]) -> dict:
        raise NotImplementedError

    def export_photos(self, items: Iterable[Any]) -> dict:
        raise NotImplementedError

    def import_orders(self) -> dict:
        raise NotImplementedError

    def import_customers(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def resolve_transport(self, entity: str, direction: str):
        """Return the configured transport for one entity and direction."""

    @abstractmethod
    def resolve_serializer(self, entity: str, direction: str):
        """Return the configured serializer for one entity and direction."""
