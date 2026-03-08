from __future__ import annotations

from abc import ABC, abstractmethod


class EcommerceProvider(ABC):
    code: str = ""

    @abstractmethod
    def is_enabled(self) -> bool:
        ...

    @abstractmethod
    def sync_orders(self) -> dict:
        ...

    @abstractmethod
    def sync_stock(self) -> dict:
        ...
