from abc import ABC, abstractmethod

class ShippingProvider(ABC):
    @abstractmethod
    def create_shipment(self, payload: dict): ...

    @abstractmethod
    def track(self, ttn: str): ...
