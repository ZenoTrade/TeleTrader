# domain\models.py
from dataclasses import dataclass
from typing import Sequence

@dataclass(frozen=True)
class Target:
    """One take-profit level."""
    price: float

@dataclass(frozen=True)
class Order:
    """Concrete order to send to trading engine."""
    symbol: str           # e.g., "XAUUSD"
    side: str             # 'buy' or 'sell'
    order_type: str       # 'limit' or 'market'
    risk: float           # in percent (risk to equity)
    price: float | None   # entry price for limit, None if market
    sl: float | None      # stop loss price or None
    tp: float | None      # take profit price or None
    comment: str          # free-text comment extracted from signal

@dataclass(frozen=True)
class Signal:
    """Parsed trading signal with multiple targets."""
    symbol: str
    side: str               # 'buy' / 'sell'
    order_type: str         # 'limit' / 'market'
    entry: float
    targets: Sequence[Target]
    stop_loss: float | None
    comment: str            # free-text comment from signal
    raw_source: str         # original message

@dataclass(frozen=True)
class OrderResult:
    """Result of sending an order."""
    success: bool
    message: str
    data: dict | None = None
