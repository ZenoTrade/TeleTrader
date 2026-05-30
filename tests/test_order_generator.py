import pytest, math, random
from tradebot.application.order_generator import (
    SimpleOrderGenerator, PropOrderManager
)
from tradebot.application.risk import (
    SimpleRiskManager, FiboRiskManager
)
from tradebot.domain.models import Signal, Target

random.seed(42)          # deterministic noise

def make_signal(targets, comment="T"):
    return Signal(
        symbol="XAUUSD", side="buy", order_type="limit",
        entry=1.5, targets=[Target(t) for t in targets],
        stop_loss=1.2, comment=comment, raw_source=""
    )

# ------------------------------------------------------------------
def test_simple_order_generator():
    mgr = SimpleRiskManager(); mgr.total_risk = lambda s: 1.0
    gen = SimpleOrderGenerator(risk_manager=mgr)
    sig = make_signal([10,20,30])
    orders = gen.generate_orders(sig)
    exp = [0.333,0.333,0.333]
    for idx,(o,e) in enumerate(zip(orders,exp),start=1):
        assert o.risk == pytest.approx(e, rel=1e-3)
        assert o.comment.endswith(f"{idx}of3")

# ------------------------------------------------------------------
def test_fibo_order_generator_reverse():
    mgr = FiboRiskManager(reverse=True)
    mgr.total_risk = lambda sig: 1.0

    gen = SimpleOrderGenerator(risk_manager=mgr)
    sig = make_signal([1,2,3,4,5], comment="Z")
    orders = gen.generate_orders(sig)

    expected = [0.417, 0.250, 0.167, 0.083, 0.083]
    assert [o.risk for o in orders] == pytest.approx(expected, rel=1e-3)

# ------------------------------------------------------------------
def test_prop_order_manager_noise_within_bounds():
    base_mgr = SimpleRiskManager(); base_mgr.total_risk = lambda s: 0.5
    prop_gen  = PropOrderManager(risk_manager=base_mgr, noise_level=0.002)  # ±0.2 %

    sig   = make_signal([11,22,33], comment="Noise")
    orders= prop_gen.generate_orders(sig)

    # Each base risk should be 0.167; ensure noise stays within ±0.0004
    base = 0.167
    for idx,o in enumerate(orders, start=1):
        assert abs(o.risk - base) <= base * 0.002 + 1e-3
        assert o.comment.endswith(f"{idx}of3")

    # Total risk should remain ~0.5
    assert math.isclose(sum(o.risk for o in orders), 0.5, rel_tol=1e-2)
