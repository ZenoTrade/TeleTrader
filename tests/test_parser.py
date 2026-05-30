import pytest
from tradebot.application.parser import BasicSignalParser

SAMPLES = [  # text, symbol, side, order_type, entry, stop_loss, targets, comment
    (
        """
        🔴 XAUUSD - SELL NOW 🔴

        🎯 Entry: 3340.00

        Targets:
        📈 3333.00
        📈 3328.00
        📈 3320.00

        🛑 Stoploss: 3345.00

        @Zeno | Trader: Lily+AI
        """,
        "XAUUSD", "sell", "market", 3340.00, 3345.00,
        [3333.00, 3328.00, 3320.00],
        "Lily+AI"
    ),
    (
        """
        🔴 XAUUSD - SELL NOW 🔴

        🎯 Entry: 3338.50

        Targets:
        📈 3334.00
        📈 3331.00
        📈 3328.00
        📈 3325.00
        📈 3322.00
        📈 3317.00

        🛑 Stoploss: 3347.00

        @Zeno | Trader: Mirbaha
        """,
        "XAUUSD", "sell", "market", 3338.50, 3347.00,
        [3334.00, 3331.00, 3328.00, 3325.00, 3322.00, 3317.00],
        "Mirbaha"
    ),
]

@pytest.mark.parametrize(
    "text,symbol,side,order_type,entry,stop_loss,targets,comment",
    SAMPLES
)
def test_parser_various(text, symbol, side, order_type, entry, stop_loss, targets, comment):
    """
    Smoke test across multiple signal formats, now checks comment too.
    """
    sig = BasicSignalParser().parse(text)
    assert sig is not None, "Parser returned None for a valid signal"
    assert sig.symbol     == symbol
    assert sig.side       == side
    assert sig.order_type == order_type
    assert sig.entry      == entry
    assert sig.stop_loss  == stop_loss
    assert [t.price for t in sig.targets] == targets
    assert sig.comment    == comment


def test_parser_invalid_returns_none():
    assert BasicSignalParser().parse("this is not a trade signal") is None
