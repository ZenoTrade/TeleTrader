import json

from tradebot.application.llm_parser import HybridSignalParser, LlmSignalParser
from tradebot.application.parser import BasicSignalParser

FREEFORM = "GOLD long now 2400-2404, SL 2390, TP1 2410 TP2 2420"


def accept(**over):
    payload = {
        "action": "accept", "confidence": 0.9, "symbol": "XAUUSD", "side": "buy",
        "order_type": "market", "entry": {"min": 2400, "max": 2404},
        "stop_loss": 2390, "take_profits": [2410, 2420], "trader": None,
    }
    payload.update(over)
    return lambda prompt: json.dumps(payload)


def test_parses_freeform_signal():
    sig = LlmSignalParser(accept()).parse(FREEFORM)
    assert sig.symbol == "XAUUSD" and sig.side == "buy" and sig.order_type == "market"
    assert sig.entry == 2402
    assert sig.stop_loss == 2390
    assert [t.price for t in sig.targets] == [2410, 2420]
    assert sig.raw_source == FREEFORM


def test_skips_llm_for_messages_without_a_trade_word():
    calls = []
    parser = LlmSignalParser(lambda p: calls.append(p) or "{}")
    assert parser.parse("Good morning traders, NFP at 8:30") is None
    assert calls == []


def test_rejects_low_confidence_and_rejections():
    assert LlmSignalParser(accept(confidence=0.5)).parse(FREEFORM) is None
    assert LlmSignalParser(accept(action="reject")).parse(FREEFORM) is None


def test_rejects_missing_stop_loss():
    assert LlmSignalParser(accept(stop_loss=None)).parse(FREEFORM) is None


def test_rejects_stop_on_wrong_side():
    assert LlmSignalParser(accept(stop_loss=2410)).parse(FREEFORM) is None
    assert LlmSignalParser(accept(side="sell")).parse(FREEFORM) is None


def test_rejects_bad_json_and_errors():
    assert LlmSignalParser(lambda p: "not json").parse(FREEFORM) is None

    def boom(prompt):
        raise TimeoutError("slow")
    assert LlmSignalParser(boom).parse(FREEFORM) is None


def test_hybrid_uses_basic_parser_first():
    calls = []
    llm = LlmSignalParser(lambda p: calls.append(p) or "{}")
    msg = "XAUUSD - BUY NOW\nEntry : 3213\nTargets :\n3216\nStoploss : 3200\n@Jasin Trader:Nemat"
    sig = HybridSignalParser(BasicSignalParser(), llm).parse(msg)
    assert sig.symbol == "XAUUSD" and sig.comment == "Nemat"
    assert calls == []


def test_hybrid_falls_back_to_llm():
    sig = HybridSignalParser(BasicSignalParser(), LlmSignalParser(accept(trader="Lily!"))).parse(FREEFORM)
    assert sig.symbol == "XAUUSD" and sig.comment == "Lily"
