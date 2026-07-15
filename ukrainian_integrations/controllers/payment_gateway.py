from abc import ABC, abstractmethod


class PaymentGateway(ABC):
    @abstractmethod
    def initiate(self, payload: dict): ...

    @abstractmethod
    def verify_callback(self, payload: dict, signature: str): ...
