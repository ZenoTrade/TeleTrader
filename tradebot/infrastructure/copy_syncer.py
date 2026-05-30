"""
Copy-trade synchronizer.

Monitors all positions on the master account and replicates every change
(open, SL/TP modify, partial close, full close) to follower accounts.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict

import MetaTrader5 as mt5
from loguru import logger

from config import settings
from ._mt5_utils import ensure_mt5
from .db import (
    get_master_user,
    get_follower_users,
    upsert_copy_map,
    get_follower_ticket,
    get_follower_tickets_for_master,
    delete_copy_map,
    delete_copy_map_by_master,
    UserAccount,
)


@dataclass
class _PosSnap:
    """Lightweight snapshot of one MT5 position."""
    ticket: int
    symbol: str
    pos_type: int       # 0=buy, 1=sell
    volume: float
    sl: float
    tp: float
    price_open: float
    comment: str


class CopyTradeSyncer:
    """Background thread that keeps follower accounts in sync with master."""

    def __init__(self, interval_sec: int = 5):
        self.interval_sec = interval_sec
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._prev: Dict[int, _PosSnap] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._loop, name="copy-syncer", daemon=True)
        self._worker.start()
        logger.info("CopyTradeSyncer started (interval={}s)", self.interval_sec)

    def stop(self) -> None:
        self._stop.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=max(self.interval_sec, 2))
        logger.info("CopyTradeSyncer stopped")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.error(f"CopyTradeSyncer error: {exc}")
            self._stop.wait(self.interval_sec)

    # ------------------------------------------------------------------
    # Main tick
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        master = get_master_user()
        if not master:
            return

        ensure_mt5(master.mt5_path)
        if not mt5.login(master.mt5_account, master.mt5_password,
                         master.mt5_server):
            logger.warning("CopyTradeSyncer: master login failed")
            return

        raw = mt5.positions_get() or []
        current: Dict[int, _PosSnap] = {}
        for p in raw:
            current[p.ticket] = _PosSnap(
                ticket=p.ticket,
                symbol=p.symbol,
                pos_type=int(p.type),
                volume=float(p.volume),
                sl=float(p.sl),
                tp=float(p.tp),
                price_open=float(p.price_open),
                comment=getattr(p, "comment", "") or "",
            )

        prev = self._prev
        followers = get_follower_users()
        if not followers:
            self._prev = current
            return

        prev_tickets = set(prev.keys())
        curr_tickets = set(current.keys())

        # --- new positions (open on followers) ---
        for tk in curr_tickets - prev_tickets:
            snap = current[tk]
            for f in followers:
                self._open_on_follower(master, f, snap)

        # --- closed positions (close on followers) ---
        for tk in prev_tickets - curr_tickets:
            self._close_on_followers(tk)

        # --- still-open positions: check modifications ---
        for tk in curr_tickets & prev_tickets:
            cur = current[tk]
            old = prev[tk]

            sl_changed = cur.sl != old.sl
            tp_changed = cur.tp != old.tp
            if sl_changed or tp_changed:
                self._modify_sltp_on_followers(tk, cur)

            if cur.volume < old.volume:
                ratio = (old.volume - cur.volume) / old.volume
                self._partial_close_on_followers(tk, cur, ratio)

        self._prev = current

    # ------------------------------------------------------------------
    # Open
    # ------------------------------------------------------------------

    def _open_on_follower(self, master: UserAccount,
                          follower: UserAccount, snap: _PosSnap) -> None:
        try:
            ensure_mt5(follower.mt5_path)
            if not mt5.login(follower.mt5_account, follower.mt5_password,
                             follower.mt5_server):
                logger.warning(
                    f"CopySyncer open: login failed for {follower.mt5_account}")
                return

            if follower.risk_mode == "fixed_lot":
                volume = self._round_volume(follower.fixed_lot, snap.symbol)
            else:
                volume = self._scale_volume(
                    snap.volume, snap.symbol,
                    master.risk_per_trade, follower.risk_per_trade)
            if volume <= 0:
                return

            side_buy = snap.pos_type == int(mt5.POSITION_TYPE_BUY)
            tick = mt5.symbol_info_tick(snap.symbol)
            if not tick:
                logger.warning(f"CopySyncer: no tick for {snap.symbol}")
                return

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": snap.symbol,
                "volume": volume,
                "type": mt5.ORDER_TYPE_BUY if side_buy else mt5.ORDER_TYPE_SELL,
                "price": tick.ask if side_buy else tick.bid,
                "sl": snap.sl,
                "tp": snap.tp,
                "deviation": follower.max_slippage,
                "magic": settings.magic_number,
                "comment": f"copy-{snap.ticket}"[:30],
                "type_time": mt5.ORDER_TIME_GTC,
            }

            res = mt5.order_send(request)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                upsert_copy_map(
                    snap.ticket, follower.mt5_account,
                    int(res.order), snap.symbol)
                logger.info(
                    f"CopySyncer: opened {snap.symbol} on "
                    f"{follower.mt5_account} ticket={res.order}")
            else:
                data = (res._asdict() if res and hasattr(res, "_asdict")
                        else mt5.last_error())
                logger.warning(
                    f"CopySyncer open failed on {follower.mt5_account}: {data}")

        except Exception as exc:
            logger.error(
                f"CopySyncer open error for {follower.mt5_account}: {exc}")

    # ------------------------------------------------------------------
    # Modify SL/TP
    # ------------------------------------------------------------------

    def _modify_sltp_on_followers(self, master_ticket: int,
                                  snap: _PosSnap) -> None:
        mappings = get_follower_tickets_for_master(master_ticket)
        for f_account, f_ticket in mappings:
            try:
                self._login_follower(f_account)
                request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "position": f_ticket,
                    "symbol": snap.symbol,
                    "sl": snap.sl,
                    "tp": snap.tp,
                }
                res = mt5.order_send(request)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info(
                        f"CopySyncer: SL/TP updated on {f_account} "
                        f"pos={f_ticket}")
                else:
                    data = (res._asdict() if res and hasattr(res, "_asdict")
                            else mt5.last_error())
                    logger.warning(
                        f"CopySyncer SLTP fail {f_account}/{f_ticket}: {data}")
            except Exception as exc:
                logger.error(
                    f"CopySyncer SLTP error {f_account}/{f_ticket}: {exc}")

    # ------------------------------------------------------------------
    # Full close
    # ------------------------------------------------------------------

    def _close_on_followers(self, master_ticket: int) -> None:
        mappings = get_follower_tickets_for_master(master_ticket)
        for f_account, f_ticket in mappings:
            try:
                self._login_follower(f_account)
                self._close_position(f_ticket, f_account)
                delete_copy_map(master_ticket, f_account)
            except Exception as exc:
                logger.error(
                    f"CopySyncer close error {f_account}/{f_ticket}: {exc}")
        delete_copy_map_by_master(master_ticket)

    # ------------------------------------------------------------------
    # Partial close
    # ------------------------------------------------------------------

    def _partial_close_on_followers(self, master_ticket: int,
                                    snap: _PosSnap,
                                    close_ratio: float) -> None:
        mappings = get_follower_tickets_for_master(master_ticket)
        for f_account, f_ticket in mappings:
            try:
                self._login_follower(f_account)

                pos = self._get_position(f_ticket)
                if not pos:
                    delete_copy_map(master_ticket, f_account)
                    continue

                close_vol = self._round_volume(
                    float(pos.volume) * close_ratio, snap.symbol)
                if close_vol <= 0:
                    continue

                side_buy = int(pos.type) == int(mt5.POSITION_TYPE_BUY)
                tick = mt5.symbol_info_tick(snap.symbol)
                if not tick:
                    continue

                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "position": f_ticket,
                    "symbol": snap.symbol,
                    "volume": close_vol,
                    "type": (mt5.ORDER_TYPE_SELL if side_buy
                             else mt5.ORDER_TYPE_BUY),
                    "price": tick.bid if side_buy else tick.ask,
                    "deviation": 20,
                    "magic": settings.magic_number,
                    "comment": "copy-partial"[:30],
                    "type_time": mt5.ORDER_TIME_GTC,
                }
                res = mt5.order_send(request)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info(
                        f"CopySyncer: partial close {close_vol} on "
                        f"{f_account} pos={f_ticket}")
                else:
                    data = (res._asdict() if res and hasattr(res, "_asdict")
                            else mt5.last_error())
                    logger.warning(
                        f"CopySyncer partial close fail "
                        f"{f_account}/{f_ticket}: {data}")
            except Exception as exc:
                logger.error(
                    f"CopySyncer partial close error "
                    f"{f_account}/{f_ticket}: {exc}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _login_follower(self, follower_account: int) -> None:
        """Login to a follower by looking up credentials from DB."""
        followers = get_follower_users()
        user = next(
            (f for f in followers if f.mt5_account == follower_account), None)
        if not user:
            raise RuntimeError(
                f"Follower {follower_account} not found in DB")
        ensure_mt5(user.mt5_path)
        if not mt5.login(user.mt5_account, user.mt5_password, user.mt5_server):
            raise RuntimeError(
                f"Login failed for follower {follower_account}")

    @staticmethod
    def _get_position(ticket: int):
        """Retrieve a single open position by ticket, or None."""
        positions = mt5.positions_get(ticket=ticket)
        if positions and len(positions) > 0:
            return positions[0]
        return None

    def _close_position(self, ticket: int, account: int) -> None:
        pos = self._get_position(ticket)
        if not pos:
            logger.debug(f"CopySyncer: pos {ticket} already closed on {account}")
            return

        side_buy = int(pos.type) == int(mt5.POSITION_TYPE_BUY)
        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            return

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": pos.symbol,
            "volume": float(pos.volume),
            "type": mt5.ORDER_TYPE_SELL if side_buy else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if side_buy else tick.ask,
            "deviation": 20,
            "magic": settings.magic_number,
            "comment": "copy-close"[:30],
            "type_time": mt5.ORDER_TIME_GTC,
        }
        res = mt5.order_send(request)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"CopySyncer: closed pos {ticket} on {account}")
        else:
            data = (res._asdict() if res and hasattr(res, "_asdict")
                    else mt5.last_error())
            logger.warning(
                f"CopySyncer close fail {account}/{ticket}: {data}")

    def _scale_volume(self, master_vol: float, symbol: str,
                      master_risk: float, follower_risk: float) -> float:
        if master_risk <= 0:
            return 0.0
        raw = master_vol * (follower_risk / master_risk)
        return self._round_volume(raw, symbol)

    @staticmethod
    def _round_volume(raw: float, symbol: str) -> float:
        info = mt5.symbol_info(symbol)
        if not info:
            return 0.0
        step = info.volume_step
        vol = round(raw / step) * step
        vol = max(vol, info.volume_min)
        vol = min(vol, info.volume_max)
        return round(vol, 8)
