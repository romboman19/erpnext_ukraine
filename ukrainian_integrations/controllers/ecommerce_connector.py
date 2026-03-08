from abc import ABC, abstractmethod

class EcommerceConnector(ABC):
    @abstractmethod
    def sync_orders(self): ...

    @abstractmethod
    def sync_stock(self): ...
