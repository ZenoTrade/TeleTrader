import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.modules.setdefault("MetaTrader5", MagicMock())

from tradebot.application.parser import BasicSignalParser  # noqa: E402
from tradebot.infrastructure import telegram_listener as tl  # noqa: E402

SIGNAL = """XAUUSD - BUY NOW
Entry : 3213
Targets :
3216
Stoploss : 3200
@Jasin Trader:Nemat"""


class Guard:
    def __init__(self, active):
        self.key = "ZENO-k"
        self.state = SimpleNamespace(reason="" if active else "expired")
        self._active = active

    def has_product(self, product):
        return self._active


def _listener(guard, monkeypatch):
    lst = tl.TelegramSignalListener.__new__(tl.TelegramSignalListener)
    lst.parser = BasicSignalParser()
    lst.generator = MagicMock()
    lst.generator.generate_orders.return_value = []
    lst.license_guard = guard
    monkeypatch.setattr(tl, "upsert_trader", lambda name: None)
    monkeypatch.setattr(tl, "is_trader_allowed", lambda name: False)
    return lst


def test_inactive_license_blocks_execution(monkeypatch):
    lst = _listener(Guard(False), monkeypatch)
    out = asyncio.run(lst.process_text(SIGNAL, from_feed=True))
    assert "license not active (expired)" in out[0]
    lst.generator.generate_orders.assert_not_called()


def test_feed_signals_skip_trader_allowlist(monkeypatch):
    lst = _listener(Guard(True), monkeypatch)
    out = asyncio.run(lst.process_text(SIGNAL, from_feed=True))
    assert out == ["Signal parsed but generated no orders."]
    lst.generator.generate_orders.assert_called_once()


def test_channel_signals_still_need_enabled_trader(monkeypatch):
    lst = _listener(None, monkeypatch)
    assert asyncio.run(lst.process_text(SIGNAL, from_feed=False)) == []
    lst.generator.generate_orders.assert_not_called()
