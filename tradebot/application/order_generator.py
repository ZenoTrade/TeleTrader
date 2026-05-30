# tradebot/application/order_generator.py


import random
from typing import List
from tradebot.domain.models import Signal, Order
from tradebot.domain.ports import OrderPort, RiskManagerPort
from tradebot.application.risk import SimpleRiskManager

class BaseOrderGenerator(OrderPort):
    """Split a multi-target Signal into per-target orders.

    Each target receives an equal fraction of the total risk volume.
    The resulting Order.comment is the original comment plus
    a target index, e.g.  "Jasin Trader:Lily T1/3".
    """

    def __init__(self, total_risk=0.01):
        self.total_risk = total_risk

    # ------------------------------------------------------------------
    def generate_orders(self, signal: Signal) -> list[Order]:
        totla_risk = self.total_risk(signal)
        n_targets = max(len(signal.targets), 1)
        if n_targets == 0:
            return []
        risk_each = round(totla_risk / n_targets, 3)

        orders: list[Order] = []
        for idx, tgt in enumerate(signal.targets, start=1):
            tgt_comment = f"{signal.comment} {idx}of{n_targets}".strip()
            orders.append(
                Order(
                    symbol       = signal.symbol,
                    side         = signal.side,
                    order_type   = signal.order_type,
                    risk         = risk_each,
                    price        = signal.entry if signal.order_type == "limit" else None,
                    sl           = signal.stop_loss,
                    tp           = tgt.price,
                    comment      = tgt_comment,
                )
            )
        return orders

class SimpleOrderGenerator(OrderPort):
    """Build one Order per target, embedding per-target risk."""

    def __init__(
            self,
            risk_manager: RiskManagerPort = SimpleRiskManager()
    ):
        self.risk_manager = risk_manager

    def generate_orders(self, signal: Signal) -> List[Order]:
        risks = self.risk_manager.per_target_risks(signal)
        orders: List[Order] = []
        for idx, (tgt, risk) in enumerate(zip(signal.targets, risks), start=1):
            comment = f"{signal.comment} {idx}of{len(risks)}".strip()
            orders.append(
                Order(
                    symbol     = signal.symbol,
                    side       = signal.side,
                    order_type = signal.order_type,
                    risk       = risk,
                    price      = signal.entry if signal.order_type=="limit" else None,
                    sl         = signal.stop_loss,
                    tp         = tgt.price,
                    comment    = comment,
                )
            )
        return orders


class PropOrderManager(OrderPort):
    """
    An OrderGenerator that splits risk via a RiskManager and then adds
    a tiny perturbation to each order's risk so they aren't identical.

    Args:
        risk_manager: provider of total & per-target risks
        noise_level: maximum relative noise fraction per order
                     (e.g. 0.001 = ±0.1%)
    """
    def __init__(
        self,
        risk_manager: RiskManagerPort = SimpleRiskManager(),
        noise_level: float = 0.001
    ):
        self.risk_manager = risk_manager
        self.noise_level = noise_level

    def generate_orders(self, signal: Signal) -> List[Order]:
        # Get the base risks per target
        base_risks = self.risk_manager.per_target_risks(signal)
        orders: List[Order] = []
        n = len(base_risks)
        if n == 0:
            return []

        for idx, (tgt, base) in enumerate(zip(signal.targets, base_risks), start=1):
            # Add tiny random noise around the base risk
            noise = random.uniform(-self.noise_level, self.noise_level) * base
            actual_risk = round(base + noise, 3)

            comment = f"{signal.comment} {idx}of{n}".strip()
            orders.append(
                Order(
                    symbol     = signal.symbol,
                    side       = signal.side,
                    order_type = signal.order_type,
                    risk       = actual_risk,
                    price      = signal.entry if signal.order_type == "limit" else None,
                    sl         = signal.stop_loss,
                    tp         = tgt.price,
                    comment    = comment,
                )
            )
        return orders