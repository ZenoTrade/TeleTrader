import pytest
from tradebot.application.risk import SimpleRiskManager, FiboRiskManager
from tradebot.domain.models import Signal, Target

# helper signal with N dummy targets
def dummy_signal(n):
    return Signal(
        symbol="EURUSD", side="buy", order_type="limit",
        entry=1.0, targets=[Target(1.0) for _ in range(n)],
        stop_loss=None, comment="c", raw_source=""
    )

def test_simple_risk_manager():
    mgr = SimpleRiskManager()
    mgr.total_risk = lambda s: 1.2       # force 1.2 %
    risks = mgr.per_target_risks(dummy_signal(3))
    assert risks == [0.4, 0.4, 0.4]

@pytest.mark.parametrize("reverse,expected", [
    (False, [0.083,0.083,0.167,0.25,0.417]),   # forward
    (True,  [0.417,0.25,0.167,0.083,0.083]),   # reverse
])
def test_fibo_risk_manager(reverse, expected):
    mgr = FiboRiskManager(reverse=reverse)
    mgr.total_risk = lambda s: 1.0
    risks = mgr.per_target_risks(dummy_signal(5))
    assert risks == pytest.approx(expected, rel=1e-3)
