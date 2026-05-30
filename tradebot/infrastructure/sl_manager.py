from __future__ import annotations

import re
import threading
import time as time_mod
from datetime import datetime

import MetaTrader5 as mt5
from loguru import logger

from config import settings
from .db import get_enabled_users, get_master_user
from ._mt5_utils import ensure_mt5
from .stop_manager import StopManager


_COMMENT_RX = re.compile(
    r"(?P<prefix>.+?)\s+(?P<idx>\d+)of(?P<total>\d+)", re.I)


def _server_now_epoch() -> float | None:
    """Current broker/server Unix seconds from tick stream, if available."""
    tick = mt5.symbol_info_tick("XAUUSD")
    if tick:
        t = getattr(tick, "time", None)
        if isinstance(t, (int, float)) and float(t) > 0:
            return float(t)
        tm = getattr(tick, "time_msc", None)
        if isinstance(tm, (int, float)) and float(tm) > 0:
            return float(tm) / 1000.0
    return None


class SignalSLManager:
    _logged_missing_history_select = False
    """
    Ladder trailing: when leg **k** of **N** (k < N) closes at take-profit, move
    stop-loss on every **still-open position** with leg index **> k** (same
    comment prefix / total / side) toward **that** leg's fill price — TP1 hit
    tightens higher legs toward TP1, TP2 hit toward TP2, and so on.

    Pending orders are not modified; unfilled siblings keep broker SL until fill.

    On MetaTrader5 builds that expose ``history_select``, we call it before
    ``history_deals_get`` so the terminal loads deals for the window. Older
    Python packages omit ``history_select``; we then rely on ``history_deals_get``
    alone.
    """

    def __init__(self, interval_sec: int = 5):
        self.interval_sec = interval_sec
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._processed_tp_deals: set[tuple[int, int]] = set()
        self._bootstrapped_accounts: set[int] = set()
        self._stop_manager = StopManager()

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._loop, name="signal-sl-manager", daemon=True)
        self._worker.start()
        logger.info("Signal SL manager started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=max(self.interval_sec, 2))
        logger.info("Signal SL manager stopped")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                logger.error(f"SL manager loop error: {exc}")
            self._stop_event.wait(self.interval_sec)

    # ------------------------------------------------------------------
    def run_once(self) -> None:
        # MT5 often returns an empty list for timezone-aware UTC datetimes.
        # Integer Unix bounds match the terminal and populate deal history reliably.
        local_now = time_mod.time()
        server_now = _server_now_epoch()
        to_ts = int(server_now if server_now is not None else local_now)
        fr_ts = to_ts - 86400 * 30
        na_to = datetime.fromtimestamp(to_ts)
        na_fr = datetime.fromtimestamp(fr_ts)
        if server_now is not None:
            skew = server_now - local_now
            if abs(skew) > 120:
                logger.debug(
                    f"SL Manager: using server time for history scan due to "
                    f"local skew {skew/60:.1f} min")

        dbp = settings.db_path
        master = get_master_user(dbp)
        users = [master] if master else get_enabled_users(dbp)

        for user in users:
            ensure_mt5(user.mt5_path)
            if not mt5.login(user.mt5_account, user.mt5_password,
                             user.mt5_server):
                logger.warning(
                    f"SL manager login failed for account {user.mt5_account}")
                continue

            history_select = getattr(mt5, "history_select", None)
            if history_select is None:
                if not SignalSLManager._logged_missing_history_select:
                    SignalSLManager._logged_missing_history_select = True
                    logger.info(
                        "SL Manager: this MetaTrader5 build has no "
                        "history_select(); using history_deals_get() only. "
                        "Upgrade the 'MetaTrader5' pip package if deal scans "
                        "are empty.")
            elif not history_select(na_fr, na_to):
                logger.warning(
                    f"SL Manager: history_select failed for "
                    f"{user.mt5_account} ({mt5.last_error()}) — "
                    f"still trying history_deals_get")

            deals = mt5.history_deals_get(fr_ts, to_ts) or []
            if not deals:
                deals = mt5.history_deals_get(na_fr, na_to) or []
            if not deals:
                try:
                    deals = mt5.history_deals_get(fr_ts, to_ts, "*") or []
                except TypeError:
                    pass
            if not deals:
                logger.debug(
                    f"SL Manager: no deals in selected window for "
                    f"{user.mt5_account}")

            tp_candidates = 0
            is_bootstrap = user.mt5_account not in self._bootstrapped_accounts
            bootstrap_added = 0
            for deal in deals:
                parsed_leg = self._parse_multi_leg_tp_close(deal)
                if parsed_leg is None:
                    continue
                tp_candidates += 1
                prefix, closed_k, total = parsed_leg
                key = (user.mt5_account, int(deal.ticket))
                if key in self._processed_tp_deals:
                    continue
                if is_bootstrap:
                    # Cold-start sync: mark historical TP deals as processed so we
                    # only act on fresh events after bot startup.
                    self._processed_tp_deals.add(key)
                    bootstrap_added += 1
                    continue

                comment = getattr(deal, "comment", "") or ""
                logger.info(
                    f"SL Manager: TP leg {closed_k}of… hit — deal {deal.ticket} "
                    f"comment='{comment}' symbol={deal.symbol} "
                    f"price={deal.price} reason={getattr(deal, 'reason', '')}")

                changed, work_left, saw_sibs = self._move_open_siblings_sl(
                    user.mt5_account, deal, prefix, total, closed_k)
                if not saw_sibs:
                    logger.info(
                        f"SL Manager: no open legs with index > {closed_k} "
                        f"for this signal on account {user.mt5_account}")
                    self._processed_tp_deals.add(key)
                elif work_left == 0:
                    if changed:
                        logger.info(
                            f"SL Manager: SL moved toward TP fill for {changed} "
                            f"leg(s) on account {user.mt5_account}")
                    self._processed_tp_deals.add(key)
                else:
                    logger.warning(
                        f"SL Manager: SL adjust still pending for {work_left} "
                        f"leg(s) (ok {changed}) — retrying "
                        f"(account {user.mt5_account})")

            if is_bootstrap:
                self._bootstrapped_accounts.add(user.mt5_account)
                logger.info(
                    f"SL Manager: warmup synced {bootstrap_added} historical TP "
                    f"deal(s) for account {user.mt5_account}; now tracking new TP events only")

            # Break-even + ATR trail (master-only at runtime; followers
            # inherit via CopyTradeSyncer mirroring SL changes).
            try:
                self._stop_manager.apply(user)
            except Exception as exc:
                logger.error(
                    f"SL Manager: stop_manager apply failed for "
                    f"account {user.mt5_account}: {exc}")

            logger.debug(
                f"SL Manager: scan summary account={user.mt5_account} "
                f"deals={len(deals)} tp_candidates={tp_candidates} "
                f"processed={len(self._processed_tp_deals)}")

    # ------------------------------------------------------------------
    @staticmethod
    def _tp_close_reason(reason: int) -> bool:
        """Brokers usually use DEAL_REASON_TP; some map TP to EXPERT/CLIENT."""
        ok = {int(mt5.DEAL_REASON_TP)}
        for name in (
                "DEAL_REASON_EXPERT",
                "DEAL_REASON_CLIENT",
                "DEAL_REASON_WEB",
                "DEAL_REASON_MOBILE",
        ):
            v = getattr(mt5, name, None)
            if v is not None:
                ok.add(int(v))
        return int(reason) in ok

    def _parse_multi_leg_tp_close(self, deal) -> tuple[str, int, int] | None:
        """If deal is TP-like close of ``k of N``, return (prefix, k, N)."""
        if int(getattr(deal, "entry", -1)) != int(mt5.DEAL_ENTRY_OUT):
            return None
        reason = int(getattr(deal, "reason", -1))
        if not self._tp_close_reason(reason):
            # Some brokers don't mark TP in reason on close; if profit is positive
            # we still consider it a TP-like close for multi-leg protection.
            profit = float(getattr(deal, "profit", 0.0) or 0.0)
            if profit <= 0:
                return None

        parsed = self._parse_comment(getattr(deal, "comment", "") or "")
        if not parsed:
            parsed = self._parse_leg_from_position_history(
                int(getattr(deal, "position_id", 0) or 0))
        if not parsed:
            return None
        _, k, n = parsed
        if n <= 1 or k < 1 or k >= n:
            return None
        return parsed[0], k, n

    def _parse_leg_from_position_history(
            self, position_id: int) -> tuple[str, int, int] | None:
        """Recover original leg comment when close deal comment is empty/mutated."""
        if position_id <= 0:
            return None
        try:
            deals = mt5.history_deals_get(position=position_id) or []
        except TypeError:
            return None
        for d in deals:
            parsed = self._parse_comment(getattr(d, "comment", "") or "")
            if parsed:
                return parsed
        return None

    def _move_open_siblings_sl(
            self,
            account_id: int,
            tp_deal,
            prefix: str,
            total: int,
            closed_leg_idx: int,
    ) -> tuple[int, int, bool]:
        """After leg ``closed_leg_idx`` closed at TP, tighten SL on legs j > idx.

        Anchor is this leg's **fill price** (TP1, TP2, …), so each TP step moves
        remaining stops up the ladder. Only **open positions** are updated.

        Return (success_count, work_remaining, saw_sibling_rows).
        """
        symbol = tp_deal.symbol
        anchor = float(tp_deal.price)
        side = ("buy" if int(tp_deal.type) == int(mt5.DEAL_TYPE_SELL)
                else "sell")

        positions = mt5.positions_get(symbol=symbol) or []
        logger.debug(
            f"SL Manager: siblings for '{prefix}' side={side} total={total} "
            f"(closed leg {closed_leg_idx}) — {len(positions)} open {symbol} "
            f"position(s)")

        changed = 0
        work_remaining = 0
        saw_sibling_rows = False
        for pos in positions:
            pos_comment = getattr(pos, "comment", "") or ""
            p = self._parse_comment(pos_comment)
            if not p:
                continue
            p_prefix, p_idx, p_total = p

            if p_prefix != prefix or p_total != total:
                continue
            if p_idx <= closed_leg_idx:
                continue

            if not self._same_side(pos, side):
                continue

            saw_sibling_rows = True
            current_sl = float(getattr(pos, "sl", 0.0) or 0.0)
            new_sl = self._clamp_sl_to_symbol_rules(
                pos.symbol, side, anchor, current_sl)
            if new_sl is None:
                continue

            sym_info = mt5.symbol_info(pos.symbol)
            point = float(getattr(sym_info, "point", 0.0) or 0.0) if sym_info else 0.0
            min_step = (point * 0.25) if point > 0 else 1e-8
            if abs(new_sl - current_sl) <= min_step:
                logger.debug(
                    f"SL Manager: pos {pos.ticket} ({pos_comment}) "
                    f"SL {current_sl} already at target band vs TP{closed_leg_idx} "
                    f"fill {anchor}, skip")
                continue

            work_remaining += 1
            logger.info(
                f"SL Manager: TP{closed_leg_idx} hit → move SL on pos {pos.ticket} "
                f"({pos_comment}) from {current_sl} to {new_sl} "
                f"(fill anchor {anchor})")

            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": pos.ticket,
                "symbol": pos.symbol,
                "sl": new_sl,
                "tp": float(getattr(pos, "tp", 0.0) or 0.0),
            }
            res = mt5.order_send(request)
            if res and int(res.retcode) == int(mt5.TRADE_RETCODE_DONE):
                changed += 1
                work_remaining -= 1
                continue

            logger.warning(
                f"SL Manager: failed SL update on pos {pos.ticket} "
                f"account {account_id}: "
                f"{res._asdict() if res and hasattr(res, '_asdict') else mt5.last_error()}"
            )

        return changed, work_remaining, saw_sibling_rows

    # ------------------------------------------------------------------
    @staticmethod
    def _clamp_sl_to_symbol_rules(
            symbol: str,
            side: str,
            anchor_price: float,
            current_sl: float,
    ) -> float | None:
        """Return an MT5-valid SL at or toward ``anchor_price``, or None.

        For **sells**, SL must stay **above** the current Ask (plus stops level).
        """
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if not info or not tick:
            d = int(getattr(info, "digits", 5) or 5) if info else 5
            return round(float(anchor_price), d)

        point = float(info.point or 1e-5)
        digits = int(info.digits or 5)
        dist = float(int(info.trade_stops_level or 0)) * point
        cur = float(current_sl or 0.0)
        anchor = float(anchor_price)

        if side == "sell":
            ask = float(tick.ask)
            min_sl = ask + dist
            cand = max(anchor, min_sl)
            if cand >= cur - point * 0.5:
                logger.warning(
                    f"SL Manager: sell SL cannot be tightened toward anchor "
                    f"(Ask={ask:.5f}, min SL≈{min_sl:.5f}, anchor={anchor:.5f}, "
                    f"current SL={cur:.5f}) — market above anchor; MT5 rejects "
                    f"SL below Ask")
                return None
            return round(cand, digits)

        bid = float(tick.bid)
        max_sl = bid - dist
        cand = min(anchor, max_sl)
        if cand <= cur + point * 0.5:
            logger.warning(
                f"SL Manager: buy SL cannot be tightened toward anchor "
                f"(Bid={bid:.5f}, max SL≈{max_sl:.5f}, anchor={anchor:.5f}, "
                f"current SL={cur:.5f})")
            return None
        return round(cand, digits)

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_comment(comment: str) -> tuple[str, int, int] | None:
        raw = (comment or "").strip()
        if raw.upper().startswith("TT|"):
            raw = raw[3:].lstrip()
        m = _COMMENT_RX.search(raw)
        if not m:
            return None
        prefix = m.group("prefix").strip().casefold()
        idx = int(m.group("idx"))
        total = int(m.group("total"))
        return prefix, idx, total

    @staticmethod
    def _same_side(pos, side: str) -> bool:
        p_type = int(getattr(pos, "type", -1))
        return ((side == "buy"
                 and p_type == int(mt5.POSITION_TYPE_BUY))
                or (side == "sell"
                    and p_type == int(mt5.POSITION_TYPE_SELL)))

