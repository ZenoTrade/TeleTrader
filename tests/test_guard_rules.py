from tradebot.domain import guard as g

RULES = g.GuardRules(daily_loss_pct=5, max_drawdown_pct=10, buffer_pct=1,
                     max_risk_per_trade_pct=1)


def state(**over):
    base = dict(initial_balance=100_000, day="2026-10-29",
                day_start_balance=100_000, high_balance=100_000)
    base.update(over)
    return g.GuardState(**base)


def snap(equity, balance=None, positions=1, open_risk=0.0):
    return g.AccountSnapshot(balance or equity, equity, positions, open_risk)


def test_guard_line_uses_daily_limit_with_buffer():
    line, name = g.guard_line(RULES, state())
    assert (line, name) == (96_000, "daily loss limit")


def test_drawdown_floor_binds_after_losing_days():
    # Day starts at 93k: daily floor 88k, drawdown floor 90k.
    line, name = g.guard_line(RULES, state(day_start_balance=93_000))
    assert (line, name) == (91_000, "max drawdown")


def test_trailing_drawdown_follows_high():
    rules = g.GuardRules(max_drawdown_pct=6, daily_loss_pct=50, trailing_drawdown=True, buffer_pct=0)
    assert g.drawdown_floor(rules, state(high_balance=108_000)) == 102_000


def test_flattens_at_guard_line():
    d = g.evaluate(RULES, state(), snap(95_999))
    assert d.flatten and not d.allow_new and d.reason == "daily loss limit"
    assert g.evaluate(RULES, state(), snap(96_500)).allow_new


def test_locked_account_stays_flat_until_next_day():
    s = g.lock(state(), "daily loss limit", "2026-10-30")
    assert g.evaluate(RULES, s, snap(99_000)).flatten
    assert not g.evaluate(RULES, s, snap(99_000, positions=0)).flatten
    assert not g.evaluate(RULES, s, snap(99_000, positions=0)).allow_new
    s2 = g.roll_day(s, "2026-10-30", 96_000)
    assert not g.is_locked(s2)
    assert s2.day_start_balance == 96_000


def test_roll_day_keeps_day_start_within_a_day():
    s = g.roll_day(state(), "2026-10-29", 97_000)
    assert s.day_start_balance == 100_000
    s = g.roll_day(state(), "2026-10-29", 104_000)
    assert s.high_balance == 104_000


def test_profit_target_locks_forever():
    rules = g.GuardRules(profit_target_pct=10)
    d = g.evaluate(rules, state(), snap(110_000))
    assert d.flatten and d.reason == "profit target reached"
    s = g.lock(state(), d.reason, "2026-10-30")
    assert g.is_locked(g.roll_day(s, "2026-11-30", 110_000))


def test_order_checks():
    s = state()
    assert g.check_order(RULES, s, snap(100_000), 500) == (True, "")
    ok, reason = g.check_order(RULES, s, snap(100_000), 1_500)
    assert not ok and "per-trade cap" in reason
    ok, reason = g.check_order(RULES, s, snap(97_000, open_risk=800), 500)
    assert not ok and "buffer" in reason
    assert g.check_order(RULES, s, snap(100_000), None) == (False, "no stop loss")
    no_sl_ok = g.GuardRules(require_stop_loss=False)
    assert g.check_order(no_sl_ok, s, snap(100_000), None) == (True, "")


def test_max_open_positions():
    rules = g.GuardRules(max_open_positions=2)
    assert not g.check_order(rules, state(), snap(100_000, positions=2), 100)[0]


def test_new_state_uses_configured_initial_balance():
    s = g.new_state(97_000, "2026-10-29", 100_000)
    assert s.initial_balance == 100_000 and s.day_start_balance == 97_000
