# domain\ports.py

from abc import ABC, abstractmethod
from .models import Signal, Order, OrderResult

class SignalParserPort(ABC):
    @abstractmethod
    def parse(self, message: str) -> Signal | None: ...

class OrderPort(ABC):
    @abstractmethod
    def generate_orders(self, signal: Signal) -> Order:...

class RiskManagerPort(ABC):
    @abstractmethod
    def total_risk(self, signal: Signal) -> float: ...
    @abstractmethod
    def per_target_risks(self, signal: Signal) -> list[float]: ...

class TradingEnginePort(ABC):
    @abstractmethod
    def execute_order(self, order: Order) -> OrderResult: ...

class NotificationPort(ABC):
    """Port for sending notifications (e.g. on shutdown or errors)."""
    @abstractmethod
    async def notify(self, message: str) -> None: ...

