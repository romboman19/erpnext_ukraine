from abc import ABC, abstractmethod


class SMSProvider(ABC):
    @abstractmethod
    def send_sms(self, phone: str, text: str): ...
