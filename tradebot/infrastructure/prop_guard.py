# tradebot/infrastructure/prop_guard.py

"""
Zeno Guard on MetaTrader 5.

Checks every enabled account every few seconds. When equity reaches the
guard line it closes every position, deletes every pending order and locks
the account until the next trading day. Before the engine or the copy
syncer sends an order, pre_trade() refuses it if its stop would take the
account past the guard line. The rules themselves live in
tradebot/domain/guard.py.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5
from loguru import logger

from config import settings
from tradebot.domain import guard as g
from ._mt5_utils import MT5_LOCK, ensure_mt5
from .db import UserAccount, get_enabled_users, get_guard_state, save_guard_state


def trading_day(now: datetime, tz: ZoneInfo) -> str:
    return now.astimezone(tz).date().isoformat()


def next_trading_day(day: str) -> str:
    return (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()


def rules_from_settings() -> g.GuardRules:
    return g.GuardRules(
        daily_loss_pct=settings.guard_daily_loss_pct,
        max_drawdown_pct=settings.guard_max_drawdown_pct,
        trailing_drawdown=settings.guard_trailing_drawdown,
        buffer_pct=settings.guard_buffer_pct,
        max_risk_per_trade_pct=settings.guard_max_risk_per_trade_pct,
        max_open_positions=settings.guard_max_open_positions,
        require_stop_loss=settings.guard_require_stop_loss,
        profit_target_pct=settings.guard_profit_target_pct,
    )


class PropGuard:
    def __init__(
        self,
        rules: g.GuardRules,
        tz: str = "Europe/Prague",
        interval_sec: float = 2,
        notify: Callable[[str], None] | None = None,
        licensed: Callable[[], bool] = lambda: True,
        initial_balance: float = 0,
        db_path: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.rules = rules
        self.tz = ZoneInfo(tz)
        self.interval_sec = interval_sec
        self._notify = notify or (lambda msg: None)
        self._licensed = licensed
        self._initial_balance = initial_balance
        self._db_path = db_path
        self._clock = clock
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._warned_unlicensed = False

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._loop, name="zeno-guard", daemon=True)
        self._worker.start()
        logger.info("Zeno Guard started: {}", self.describe())

    def stop(self) -> None:
        self._stop.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=max(self.interval_sec, 2))

    def describe(self) -> str:
        r = self.rules
        dd = "trailing" if r.trailing_drawdown else "static"
        return (f"daily loss {r.daily_loss_pct:g}%, max drawdown {r.max_drawdown_pct:g}% ({dd}), "
                f"buffer {r.buffer_pct:g}%, max risk per trade {r.max_risk_per_trade_pct:g}%")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:
                logger.error(f"Zeno Guard error: {exc}")
            self._stop.wait(self.interval_sec)

    # ------------------------------------------------------------------
    def run_once(self) -> None:
        if not self._licensed():
            if not self._warned_unlicensed:
                self._warned_unlicensed = True
                self._notify("Zeno Guard is off: the license does not include Guard or has expired.")
            return
        self._warned_unlicensed = False
        for user in get_enabled_users(self._db_path):
            with MT5_LOCK:
                if self._login(user):
                    self.check_account(user)

    def check_account(self, user: UserAccount) -> g.GuardDecision | None:
        """Evaluate the account MT5 is logged into now and act on it."""
        snap = self._snapshot()
        if snap is None:
            return None
        state = self._state(user, snap.balance)
        decision = g.evaluate(self.rules, state, snap)
        if decision.flatten:
            if not g.is_locked(state):
                state = g.lock(state, decision.reason, next_trading_day(state.day))
                self._notify(
                    f"🛡️ Zeno Guard: {decision.reason} on {user.mt5_account}. "
                    f"Closing all trades. Equity {snap.equity:.2f}. "
                    f"Trading locked {self._lock_text(state)}.")
            closed = self._flatten()
            if closed:
                logger.warning(f"Zeno Guard flattened {user.mt5_account}: {closed} closed/removed")
        save_guard_state(user.mt5_account, state, self._db_path)
        return decision

    def pre_trade(self, user: UserAccount, symbol: str, side: str, volume: float,
                  price: float, sl: float | None) -> tuple[bool, str]:
        """Called with MT5 logged into `user`, before an order is sent."""
        if not self._licensed():
            return True, ""
        snap = self._snapshot()
        if snap is None:
            return False, "no account data"
        state = self._state(user, snap.balance)
        risk = self._loss_to_stop(symbol, side == "buy", volume, price, sl) if sl else None
        ok, reason = g.check_order(self.rules, state, snap, risk)
        save_guard_state(user.mt5_account, state, self._db_path)
        if not ok:
            logger.warning(f"Zeno Guard blocked {side} {volume} {symbol} on {user.mt5_account}: {reason}")
        return ok, reason

    # ------------------------------------------------------------------
    def _state(self, user: UserAccount, balance: float) -> g.GuardState:
        day = trading_day(self._clock(), self.tz)
        state = get_guard_state(user.mt5_account, self._db_path)
        if state is None:
            state = g.new_state(balance, day, self._initial_balance or None)
        return g.roll_day(state, day, balance)

    @staticmethod
    def _lock_text(state: g.GuardState) -> str:
        if state.locked_until == g.LOCK_FOREVER:
            return "until you reset it"
        return f"until the trading day {state.locked_until} starts"

    @staticmethod
    def _login(user: UserAccount) -> bool:
        ensure_mt5(user.mt5_path)
        if mt5.login(user.mt5_account, user.mt5_password, user.mt5_server):
            return True
        logger.warning(f"Zeno Guard: login failed for {user.mt5_account}")
        return False

    def _snapshot(self) -> g.AccountSnapshot | None:
        info = mt5.account_info()
        if info is None:
            return None
        positions = mt5.positions_get() or []
        orders = mt5.orders_get() or []
        open_risk = 0.0
        for p in positions:
            if p.sl:
                is_buy = int(p.type) == int(mt5.POSITION_TYPE_BUY)
                open_risk += self._loss_to_stop(p.symbol, is_buy, p.volume, p.price_current, p.sl)
        for o in orders:
            if o.sl:
                is_buy = int(o.type) in (int(mt5.ORDER_TYPE_BUY_LIMIT), int(mt5.ORDER_TYPE_BUY_STOP))
                open_risk += self._loss_to_stop(o.symbol, is_buy, o.volume_current, o.price_open, o.sl)
        return g.AccountSnapshot(float(info.balance), float(info.equity), len(positions), open_risk)

    @staticmethod
    def _loss_to_stop(symbol: str, is_buy: bool, volume: float, price: float, sl: float) -> float:
        kind = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        pnl = mt5.order_calc_profit(kind, symbol, float(volume), float(price), float(sl))
        return max(0.0, -float(pnl)) if pnl is not None else 0.0

    def _flatten(self) -> int:
        done = 0
        for o in mt5.orders_get() or []:
            res = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
            done += bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)
        for p in mt5.positions_get() or []:
            tick = mt5.symbol_info_tick(p.symbol)
            if not tick:
                continue
            is_buy = int(p.type) == int(mt5.POSITION_TYPE_BUY)
            res = mt5.order_send({
                "action": mt5.TRADE_ACTION_DEAL,
                "position": p.ticket,
                "symbol": p.symbol,
                "volume": float(p.volume),
                "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                "price": tick.bid if is_buy else tick.ask,
                "deviation": 50,
                "magic": settings.magic_number,
                "comment": "zeno-guard",
                "type_time": mt5.ORDER_TIME_GTC,
            })
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                done += 1
            else:
                logger.error(f"Zeno Guard could not close {p.ticket}: {mt5.last_error()}")
        return done
