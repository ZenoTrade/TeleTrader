# tradebot/application/risk.py

from typing import List
from config import settings
from tradebot.domain.models import Signal
from tradebot.domain.ports import RiskManagerPort

class SimpleRiskManager(RiskManagerPort):
    """Uses a fixed % of equity, split evenly across targets."""

    def total_risk(self, signal: Signal) -> float:
        # round to 3 decimals by default
        return round(settings.risk_per_trade, 3)

    def per_target_risks(self, signal: Signal) -> List[float]:
        total = self.total_risk(signal)
        n = len(signal.targets)
        if n == 0:
            return []
        base = round(total / n, 3)
        return [base for _ in range(n)]




class FiboRiskManager(RiskManagerPort):
    """
    Splits total risk according to the first N Fibonacci numbers.
    Optionally reverses the order (largest risk first).
    """
    def __init__(self, reverse: bool = False):
        self.reverse = reverse

    def total_risk(self, signal: Signal) -> float:
        return round(settings.risk_per_trade, 3)

    def per_target_risks(self, signal: Signal) -> List[float]:
        n = len(signal.targets)
        if n == 0:
            return []

        fibs = []
        a, b = 1, 1
        for _ in range(n):
            fibs.append(a)
            a, b = b, a + b

        s = sum(fibs)
        total = self.total_risk(signal)


        weights = [round((f / s) * total, 3) for f in fibs]

        if self.reverse:
            weights.reverse()

        # due to rounding the sum may be slightly off; adjust last element
        diff = round(total - sum(weights), 3)
        if weights:
            weights[-1] = round(weights[-1] + diff, 3)

        return weights