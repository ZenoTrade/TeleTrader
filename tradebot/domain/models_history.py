from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass(frozen=True)
class Position:
    position_id: int
    account_id: int
    magic: Optional[int]

    symbol: str
    side: str                      # buy / sell
    volume: float                  # opened volume (lots)

    price_open: float              # VWAP of entries
    price_close: Optional[float]   # VWAP of exits (None if still open)
    sl: Optional[float]
    tp: Optional[float]

    profit: float
    commission: float

    time_open: datetime
    time_close: Optional[datetime]
    duration_sec: Optional[int]    # (time_close-time_open).seconds
    fill_duration_sec: Optional[int]  # seconds from order placement to first fill (limit orders)

    rr: Optional[float]            # risk-to-reward ratio
    trader: Optional[str]
    target_index: Optional[int]
    target_total: Optional[int]
