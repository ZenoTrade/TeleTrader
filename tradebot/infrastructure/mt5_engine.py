# tradebot\infrastructure\mt5_engine.py

from dataclasses import replace
import time

import MetaTrader5 as mt5
from loguru import logger

from tradebot.domain.models import Order, OrderResult
from tradebot.domain.ports import TradingEnginePort
from ._mt5_symbol_resolver import SymbolResolver
from ._magic import MagicGen
from config import settings
from .db import (
    get_enabled_users,
    get_master_user,
    record_bot_pending_placed,
    UserAccount,
)
from ._mt5_utils import ensure_mt5
from .pending_expirer import (
    BOT_PENDING_COMMENT_PREFIX,
    MT5_ORDER_COMMENT_MAX_LEN,
)


class MetaTraderEngine(TradingEnginePort):
    def __init__(self):
        pass

    # ------------------------------------------------------------------
    def execute_order(self, order: Order) -> list[OrderResult]:
        """Execute order on the master account (CopySyncer handles followers).

        If no master is configured, falls back to executing on all enabled users.
        """
        master = get_master_user()
        users = [master] if master else get_enabled_users()
        results: list[OrderResult] = []
        for user in users:
            try:
                ensure_mt5(user.mt5_path)
                if not mt5.login(user.mt5_account, user.mt5_password,
                                 user.mt5_server):
                    logger.warning(f"Login failed for account {user.mt5_account}")
                    results.append(
                        OrderResult(False, f"Login failed for {user.mt5_account}"))
                    continue

                if user.risk_mode == "fixed_lot":
                    scaled = order
                else:
                    scaled = self._scale_order_risk(order, user.risk_per_trade)
                logger.info(f"Executing order on account {user.mt5_account} "
                            f"(mode={user.risk_mode})")
                result = self._execute_for_user(scaled, user)
                results.append(result)
            except Exception as e:
                logger.error(
                    f"Error executing order for account {user.mt5_account}: {e}")
                results.append(
                    OrderResult(False, f"Error for account {user.mt5_account}: {e}"))
        return results

    # ------------------------------------------------------------------
    @staticmethod
    def _scale_order_risk(order: Order, user_risk: float) -> Order:
        """Scale order risk proportionally to the user's personal setting."""
        global_risk = settings.risk_per_trade
        if global_risk <= 0:
            return order
        scale = user_risk / global_risk
        return replace(order, risk=round(order.risk * scale, 6))

    # ------------------------------------------------------------------
    def _execute_for_user(self, order: Order, user: UserAccount) -> OrderResult:
        resolver = SymbolResolver(path=user.mt5_path)
        symbol_mt = resolver.resolve(order.symbol)
        if not mt5.symbol_select(symbol_mt, True):
            return OrderResult(False, f"cannot select {symbol_mt}")

        if user.risk_mode == "fixed_lot":
            volume = self._clamp_volume(user.fixed_lot, symbol_mt)
            logger.debug(f"Fixed lot mode — using {volume} lots for "
                         f"{user.mt5_account}")
        else:
            volume = self._calc_volume(order, symbol_mt)

        if volume <= 0:
            return OrderResult(False, "volume calc returned 0")

        bid, ask = self._current_prices(symbol_mt)

        if order.order_type == "limit":
            action = mt5.TRADE_ACTION_PENDING

            if order.side == "buy":
                if order.price is not None and order.price <= ask:
                    mt5_type = mt5.ORDER_TYPE_BUY_LIMIT
                else:
                    mt5_type = mt5.ORDER_TYPE_BUY_STOP
            else:
                if order.price is not None and order.price >= bid:
                    mt5_type = mt5.ORDER_TYPE_SELL_LIMIT
                else:
                    mt5_type = mt5.ORDER_TYPE_SELL_STOP

            price = order.price
            if price is None:
                price = ask if order.side == "buy" else bid

        else:
            action = mt5.TRADE_ACTION_DEAL
            if order.side == "buy":
                mt5_type = mt5.ORDER_TYPE_BUY
                price    = ask
            else:
                mt5_type = mt5.ORDER_TYPE_SELL
                price    = bid

        raw_c = (order.comment or "").strip()
        if action == mt5.TRADE_ACTION_PENDING:
            tagged = (
                f"{BOT_PENDING_COMMENT_PREFIX}{raw_c}" if raw_c
                else BOT_PENDING_COMMENT_PREFIX)
            comment = tagged[:MT5_ORDER_COMMENT_MAX_LEN]
        else:
            comment = raw_c[:MT5_ORDER_COMMENT_MAX_LEN]

        request = dict(
            action     = action,
            symbol     = symbol_mt,
            volume     = volume,
            type       = mt5_type,
            price      = price,
            sl         = order.sl or 0,
            tp         = order.tp or 0,
            deviation  = user.max_slippage,
            magic      = MagicGen.generate(),
            comment    = comment,
            type_time  = mt5.ORDER_TIME_GTC,
        )
        logger.debug(f"Sending MT5 request: {request}")

        res = mt5.order_send(request)
        data = res._asdict() if res and hasattr(res, "_asdict") else None
        if isinstance(data, dict):
            data["account_id"] = user.mt5_account

        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"Order sent OK — ticket {res.order}")
            if action == mt5.TRADE_ACTION_PENDING:
                record_bot_pending_placed(
                    user.mt5_account, int(res.order), time.time())
            return OrderResult(True, "executed", data=data)

        logger.error(f"Order failed: {data} | last_error: {mt5.last_error()}")
        return OrderResult(False, "mt5 error", data=data)

    # ------------------------------------------------------------------
    def _current_order_price(self, order: Order, symbol_mt: str) -> float:
        tick = mt5.symbol_info_tick(symbol_mt)
        if not tick:
            raise RuntimeError(f"No tick for {symbol_mt}")
        return tick.ask if order.side == "buy" else tick.bid

    def _current_prices(self, symbol_mt: str) -> tuple[float, float]:
        tick = mt5.symbol_info_tick(symbol_mt)
        if not tick:
            raise RuntimeError(f"No tick for {symbol_mt}")
        return tick.bid, tick.ask

    # ------------------------------------------------------------------
    @staticmethod
    def _clamp_volume(desired: float, symbol_mt: str) -> float:
        """Clamp a raw lot size to the symbol's step/min/max constraints."""
        sym_info = mt5.symbol_info(symbol_mt)
        if sym_info is None:
            return 0.0
        step = sym_info.volume_step
        vol = round(desired / step) * step
        vol = max(vol, sym_info.volume_min)
        vol = min(vol, sym_info.volume_max)
        return round(vol, 8)

    # ------------------------------------------------------------------
    def _calc_volume(self, order: Order, symbol_mt: str) -> float:
        """
        Convert risk (percent of balance) into lots.
        volume = (risk_money) / (monetary_value_per_point * stop_distance_points)
        """

        acc = mt5.account_info()
        balance = acc.balance if acc else 0
        if balance == 0:
            return 0

        sym_info = mt5.symbol_info(symbol_mt)
        if sym_info is None:
            return 0

        money_per_point = (sym_info.trade_tick_value
                           / sym_info.trade_tick_size) * sym_info.point

        if order.sl:
            stop_distance = abs(
                (order.price or self._current_order_price(order, symbol_mt))
                - order.sl
            ) / sym_info.point
        else:
            stop_distance = 0

        if stop_distance == 0:
            return 0

        risk_money   = balance * order.risk
        raw_volume   = risk_money / (money_per_point * stop_distance)

        step   = sym_info.volume_step
        round_volume = round(raw_volume / step) * step
        volume = max(round_volume, sym_info.volume_min)
        logger.debug(
            f"For the Risk {round_volume} we should {order.side} {volume} lots!")
        return round(volume, 2)
