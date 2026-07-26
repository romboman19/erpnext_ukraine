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

    @abstractmethod
    def sync_customers(self) -> dict:
        ...

    @abstractmethod
    def sync_catalog(self) -> dict:
        ...

    @abstractmethod
    def sync_order_statuses(self) -> dict:
        ...

    def test_connection(self) -> dict:
        return {"ok": True, "skipped": True, "reason": "connection test is not implemented"}
