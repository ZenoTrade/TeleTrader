import pandas as pd, MetaTrader5 as mt5
from datetime import datetime, timedelta, timezone
from loguru import logger
from ._mt5_utils import ensure_mt5
from .db import get_enabled_users
from tradebot.domain.ports_history import TradeDataPort

class MT5HistoryRepository(TradeDataPort):
    """Fetch both deals & orders once across all configured accounts."""

    def __init__(self):
        logger.info("MT5 history repository ready")

    # ---------------------------------
    def _pull(self):
        utc_to = datetime.now(timezone.utc)
        utc_fr = utc_to - timedelta(days=365)

        all_deals, all_orders = [], []
        users = get_enabled_users()
        if not users:
            logger.warning("No enabled users in DB; cannot pull history")
            self._deals = pd.DataFrame()
            self._orders = pd.DataFrame()
            return

        for user in users:
            try:
                ensure_mt5(user.mt5_path)
                if not mt5.login(user.mt5_account, user.mt5_password,
                                 user.mt5_server):
                    logger.warning(
                        f"Login failed for account {user.mt5_account}")
                    continue

                logger.info(
                    f"Pulling history for account {user.mt5_account}...")
                deals  = mt5.history_deals_get(utc_fr, utc_to) or []
                orders = mt5.history_orders_get(utc_fr, utc_to) or []

                if deals:
                    df_d = pd.DataFrame(d._asdict() for d in deals)
                    df_d["account_id"] = user.mt5_account
                    all_deals.append(df_d)
                if orders:
                    df_o = pd.DataFrame(o._asdict() for o in orders)
                    df_o["account_id"] = user.mt5_account
                    all_orders.append(df_o)
            except Exception as e:
                logger.error(
                    f"History fetch error for {user.mt5_account}: {e}")
                continue

        df_deals  = (pd.concat(all_deals, ignore_index=True)
                     if all_deals else pd.DataFrame())
        df_orders = (pd.concat(all_orders, ignore_index=True)
                     if all_orders else pd.DataFrame())

        if not df_deals.empty:
            df_deals["time"] = pd.to_datetime(
                df_deals["time"], unit="s", utc=True)
            df_deals.set_index("time", inplace=True)

        if not df_orders.empty:
            df_orders["time_setup"] = pd.to_datetime(
                df_orders["time_setup"], unit="s", utc=True)
            df_orders.set_index("time_setup", inplace=True)

        self._deals, self._orders = df_deals, df_orders

    # ---------------------------------
    def fetch_deals(self) -> pd.DataFrame:
        if not hasattr(self, "_deals"):
            self._pull()
        return self._deals.copy()

    def fetch_orders(self) -> pd.DataFrame:
        if not hasattr(self, "_orders"):
            self._pull()
        return self._orders.copy()
