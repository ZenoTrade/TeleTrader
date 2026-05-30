"""
Per-position stop-management for the master account.

Two behaviours, both opt-in via the master user's settings:

* **Break-even (pre-TP1):** when an open position has moved in favour by
  ``be_trigger_pct`` of its original risk distance (entry .. SL), slide SL to
  ``entry ± be_cushion_points``.
* **ATR trail (post-TP1):** once SL is already on the profit side of entry
  (either by the existing TP-ladder in :class:`SignalSLManager` or by the BE
  step above), trail SL behind market by ``mult × ATR(M5, 14)`` where ``mult``
  comes from ``trail_mode`` (tight=0.5, medium=1.0, loose=1.5). SL is moved
  only forward (closer to price), only when the change is at least
  ``trail_min_step_points`` and never violating ``trade_stops_level``.

Followers don't need their own copy of this logic: the existing
:class:`tradebot.infrastructure.copy_syncer.CopyTradeSyncer` mirrors any SL
change on the master to all followers within its next tick.

Only positions whose comment matches the TT signal pattern (``prefix NofM``,
with the optional ``TT|`` tag) are touched, so manual trades on the master
account are not affected.
"""

from __future__ import annotations

import re

import MetaTrader5 as mt5
from loguru import logger


_COMMENT_RX = re.compile(
    r"(?P<prefix>.+?)\s+(?P<idx>\d+)of(?P<total>\d+)", re.I)

_TRAIL_MULT: dict[str, float] = {
    "tight": 0.5,
    "medium": 1.0,
    "loose": 1.5,
}

_ATR_PERIOD = 14
_ATR_TIMEFRAME = mt5.TIMEFRAME_M5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_tt_signal_position(pos) -> bool:
    """True when the position comment matches the bot's ``NofM`` pattern."""
    raw = (getattr(pos, "comment", "") or "").strip()
    if raw.upper().startswith("TT|"):
        raw = raw[3:].lstrip()
    return _COMMENT_RX.search(raw) is not None


def _atr_m5(symbol: str, period: int = _ATR_PERIOD) -> float | None:
    """Return ATR over the last ``period`` M5 bars, in price units."""
    needed = period + 1
    bars = mt5.copy_rates_from_pos(symbol, _ATR_TIMEFRAME, 0, needed)
    if bars is None or len(bars) < needed:
        return None
    trs: list[float] = []
    for i in range(1, len(bars)):
        high = float(bars[i]["high"])
        low = float(bars[i]["low"])
        prev_close = float(bars[i - 1]["close"])
        trs.append(max(high - low,
                       abs(high - prev_close),
                       abs(low - prev_close)))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def _clamp_sl_to_stops_level(
    symbol: str,
    side: str,
    desired_sl: float,
    current_sl: float,
) -> float | None:
    """Snap ``desired_sl`` to the nearest broker-legal value or return None.

    Mirrors :meth:`SignalSLManager._clamp_sl_to_symbol_rules` but kept local so
    a future refactor doesn't break the import boundary. Refuses to return a
    value that would not tighten the stop (i.e., move it toward price).
    """
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if not info or not tick:
        return None

    point = float(info.point or 1e-5)
    digits = int(info.digits or 5)
    stops_dist = float(int(info.trade_stops_level or 0)) * point
    cur = float(current_sl or 0.0)

    if side == "sell":
        ask = float(tick.ask)
        min_sl = ask + stops_dist
        cand = max(desired_sl, min_sl)
        if cur > 0 and cand >= cur - point * 0.5:
            return None
        return round(cand, digits)

    bid = float(tick.bid)
    max_sl = bid - stops_dist
    cand = min(desired_sl, max_sl)
    if cur > 0 and cand <= cur + point * 0.5:
        return None
    return round(cand, digits)


def _send_sltp(pos, new_sl: float) -> bool:
    """Modify SL on a position; keeps existing TP. Returns True on success."""
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": int(pos.ticket),
        "symbol": pos.symbol,
        "sl": float(new_sl),
        "tp": float(getattr(pos, "tp", 0.0) or 0.0),
    }
    res = mt5.order_send(request)
    if res and int(res.retcode) == int(mt5.TRADE_RETCODE_DONE):
        return True
    err = (res._asdict() if res and hasattr(res, "_asdict")
           else mt5.last_error())
    logger.warning(
        f"StopManager: SLTP modify rejected on pos {pos.ticket}: {err}")
    return False


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class StopManager:
    """Apply BE and trailing logic for one user (typically the master)."""

    def apply(self, user) -> None:
        """Scan open positions and update SL where appropriate."""
        be_on = bool(getattr(user, "be_enabled", False))
        trail_on = bool(getattr(user, "trail_enabled", False))
        if not be_on and not trail_on:
            return

        positions = mt5.positions_get() or []
        for pos in positions:
            if not _is_tt_signal_position(pos):
                continue
            try:
                self._apply_one(user, pos, be_on, trail_on)
            except Exception as exc:
                logger.error(
                    f"StopManager: error on pos {getattr(pos, 'ticket', '?')}"
                    f": {exc}")

    # ------------------------------------------------------------------
    def _apply_one(self, user, pos, be_on: bool, trail_on: bool) -> None:
        is_buy = int(pos.type) == int(mt5.POSITION_TYPE_BUY)
        entry = float(pos.price_open)
        current_sl = float(pos.sl or 0.0)

        info = mt5.symbol_info(pos.symbol)
        tick = mt5.symbol_info_tick(pos.symbol)
        if not info or not tick:
            return

        point = float(info.point or 0.0)
        if point <= 0:
            return
        digits = int(info.digits or 5)

        if is_buy:
            price = float(tick.bid)
            in_profit_mode = current_sl > entry + point * 0.5
        else:
            price = float(tick.ask)
            in_profit_mode = (
                current_sl > 0 and current_sl < entry - point * 0.5)

        if in_profit_mode:
            if trail_on:
                self._maybe_trail(
                    user, pos, price, is_buy, current_sl, point, digits)
        else:
            if be_on:
                self._maybe_break_even(
                    user, pos, price, entry, current_sl,
                    is_buy, point, digits)

    # ------------------------------------------------------------------
    def _maybe_break_even(
        self,
        user,
        pos,
        price: float,
        entry: float,
        current_sl: float,
        is_buy: bool,
        point: float,
        digits: int,
    ) -> None:
        if current_sl <= 0:
            # No protective stop on the position; refuse to invent one.
            return

        risk_pts = abs(entry - current_sl)
        if risk_pts <= 0:
            return

        favor_pts = (price - entry) if is_buy else (entry - price)
        trigger_pct = max(0.0, min(100.0,
                                   float(getattr(user, "be_trigger_pct", 50.0))))
        threshold = risk_pts * (trigger_pct / 100.0)
        if favor_pts < threshold:
            return

        cushion = max(0, int(getattr(user, "be_cushion_points", 5))) * point
        desired_sl = (entry + cushion) if is_buy else (entry - cushion)

        clamped = _clamp_sl_to_stops_level(
            pos.symbol,
            "buy" if is_buy else "sell",
            desired_sl,
            current_sl,
        )
        if clamped is None:
            return

        if _send_sltp(pos, clamped):
            logger.info(
                f"StopManager: BE armed on pos {pos.ticket} "
                f"({pos.comment}) — favor {favor_pts / point:.0f}pts "
                f"≥ {trigger_pct:.0f}%×{risk_pts / point:.0f}pts; "
                f"SL {current_sl:.{digits}f} → {clamped:.{digits}f}")

    # ------------------------------------------------------------------
    def _maybe_trail(
        self,
        user,
        pos,
        price: float,
        is_buy: bool,
        current_sl: float,
        point: float,
        digits: int,
    ) -> None:
        mode = (getattr(user, "trail_mode", "tight") or "tight").lower()
        mult = _TRAIL_MULT.get(mode, _TRAIL_MULT["tight"])

        atr = _atr_m5(pos.symbol)
        if atr is None or atr <= 0:
            logger.debug(
                f"StopManager: no ATR for {pos.symbol}; skipping trail "
                f"on pos {pos.ticket}")
            return

        trail_dist = atr * mult
        desired_sl = (price - trail_dist) if is_buy else (price + trail_dist)

        min_step = max(0, int(getattr(user, "trail_min_step_points", 5))) * point
        if is_buy:
            if desired_sl <= current_sl + min_step:
                return
        else:
            if current_sl <= 0 or desired_sl >= current_sl - min_step:
                return

        clamped = _clamp_sl_to_stops_level(
            pos.symbol,
            "buy" if is_buy else "sell",
            desired_sl,
            current_sl,
        )
        if clamped is None:
            return

        # Re-verify min step after broker clamp, so we don't spam tiny modifies.
        if is_buy and clamped <= current_sl + min_step:
            return
        if not is_buy and clamped >= current_sl - min_step:
            return

        if _send_sltp(pos, clamped):
            logger.info(
                f"StopManager: TRAIL on pos {pos.ticket} ({pos.comment}) — "
                f"mode={mode} ATR={atr / point:.1f}pts × {mult} = "
                f"{trail_dist / point:.1f}pts; "
                f"SL {current_sl:.{digits}f} → {clamped:.{digits}f}")
