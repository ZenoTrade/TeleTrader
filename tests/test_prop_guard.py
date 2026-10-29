import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.modules.setdefault("MetaTrader5", MagicMock())

from tradebot.domain.guard import GuardRules  # noqa: E402
from tradebot.infrastructure import db  # noqa: E402
from tradebot.infrastructure import prop_guard as pg  # noqa: E402


class FakeMT5:
    POSITION_TYPE_BUY = 0
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_BUY_STOP = 4
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_REMOVE = 8
    TRADE_RETCODE_DONE = 10009
    ORDER_TIME_GTC = 0

    def __init__(self, balance, equity, positions=(), orders=()):
        self.balance, self.equity = balance, equity
        self.positions, self.orders = list(positions), list(orders)
        self.sent = []

    def account_info(self):
        return SimpleNamespace(balance=self.balance, equity=self.equity)

    def positions_get(self):
        return list(self.positions)

    def orders_get(self):
        return list(self.orders)

    def order_calc_profit(self, kind, symbol, volume, price, sl):
        # 100 money per point per lot
        move = (sl - price) if kind == self.ORDER_TYPE_BUY else (price - sl)
        return move * volume * 100

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=1.0, ask=1.1)

    def order_send(self, request):
        self.sent.append(request)
        if request["action"] == self.TRADE_ACTION_DEAL:
            self.positions = [p for p in self.positions if p.ticket != request["position"]]
        else:
            self.orders = [o for o in self.orders if o.ticket != request["order"]]
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE)

    def last_error(self):
        return (0, "")


def position(ticket, sl=0.0, price=2400.0, volume=1.0):
    return SimpleNamespace(ticket=ticket, symbol="XAUUSD", type=0, volume=volume,
                           price_current=price, sl=sl)


USER = SimpleNamespace(mt5_account=111, mt5_password="", mt5_server="", mt5_path="")
RULES = GuardRules(daily_loss_pct=5, max_drawdown_pct=10, buffer_pct=1, max_risk_per_trade_pct=1)


def make(tmp_path, monkeypatch, fake, when="2026-10-29T10:00:00+00:00", **kw):
    dbp = str(tmp_path / "users.db")
    db.init_db(dbp)
    monkeypatch.setattr(pg, "mt5", fake)
    notes = []
    guard = pg.PropGuard(RULES, notify=notes.append, db_path=dbp,
                         clock=lambda: datetime.fromisoformat(when), **kw)
    return guard, notes, dbp


def test_flattens_and_locks_at_the_guard_line(tmp_path, monkeypatch):
    fake = FakeMT5(100_000, 100_000, positions=[position(1)],
                   orders=[SimpleNamespace(ticket=9, symbol="XAUUSD", type=2, volume_current=1,
                                           price_open=2390, sl=0)])
    guard, notes, dbp = make(tmp_path, monkeypatch, fake)
    assert guard.check_account(USER).allow_new

    fake.equity = 95_900
    d = guard.check_account(USER)
    assert d.flatten and fake.positions == [] and fake.orders == []
    assert len(notes) == 1 and "daily loss limit" in notes[0]
    state = db.get_guard_state(111, dbp)
    assert state.locked_until == "2026-10-30"

    # Still locked later that day, even after equity recovers; no repeat alert.
    fake.equity = 99_000
    fake.positions = [position(2)]
    assert guard.check_account(USER).flatten
    assert fake.positions == [] and len(notes) == 1


def test_lock_clears_on_the_next_trading_day(tmp_path, monkeypatch):
    fake = FakeMT5(100_000, 95_000)
    guard, _, dbp = make(tmp_path, monkeypatch, fake)
    guard.check_account(USER)
    # 22:30 UTC on the 29th is 23:30 in Prague: still the same trading day.
    late, _, _ = make(tmp_path, monkeypatch, fake, when="2026-10-29T22:30:00+00:00")
    fake.balance = fake.equity = 95_000
    assert not late.check_account(USER).allow_new
    nxt, _, _ = make(tmp_path, monkeypatch, fake, when="2026-10-29T23:30:00+00:00")
    assert nxt.check_account(USER).allow_new
    assert db.get_guard_state(111, dbp).day_start_balance == 95_000


def test_pre_trade_blocks_orders_whose_stop_crosses_the_buffer(tmp_path, monkeypatch):
    fake = FakeMT5(100_000, 97_500, positions=[position(1, sl=2395, price=2400)])  # 500 at risk
    guard, _, _ = make(tmp_path, monkeypatch, fake)
    assert guard.pre_trade(USER, "XAUUSD", "buy", 1.0, 2400, 2395) == (True, "")
    ok, reason = guard.pre_trade(USER, "XAUUSD", "buy", 1.0, 2400, 2390)  # 1000 more
    assert not ok and "buffer" in reason
    ok, reason = guard.pre_trade(USER, "XAUUSD", "buy", 1.0, 2400, None)
    assert (ok, reason) == (False, "no stop loss")


def test_uses_configured_initial_balance(tmp_path, monkeypatch):
    fake = FakeMT5(97_000, 97_000)
    guard, _, dbp = make(tmp_path, monkeypatch, fake, initial_balance=100_000)
    guard.check_account(USER)
    assert db.get_guard_state(111, dbp).initial_balance == 100_000


def test_does_nothing_without_a_guard_license(tmp_path, monkeypatch):
    fake = FakeMT5(100_000, 90_000, positions=[position(1)])
    guard, notes, _ = make(tmp_path, monkeypatch, fake, licensed=lambda: False)
    monkeypatch.setattr(pg, "get_enabled_users", lambda path: [USER])
    guard.run_once()
    guard.run_once()
    assert fake.positions and len(notes) == 1
    assert guard.pre_trade(USER, "XAUUSD", "buy", 1.0, 2400, None) == (True, "")


def test_trading_day_follows_the_reset_zone():
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Prague")
    assert pg.trading_day(datetime(2026, 10, 29, 22, 59, tzinfo=timezone.utc), tz) == "2026-10-29"
    assert pg.trading_day(datetime(2026, 10, 29, 23, 0, tzinfo=timezone.utc), tz) == "2026-10-30"
