# tradebot/domain/ports_history.py
from abc import ABC, abstractmethod
import pandas as pd

class TradeDataPort(ABC):
    """Provide raw MT5 deals and orders"""

    @abstractmethod
    def fetch_deals(self) -> pd.DataFrame:  ...   # must include position_id
    @abstractmethod
    def fetch_orders(self) -> pd.DataFrame: ...   # must include position_id
