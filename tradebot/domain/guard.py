# tradebot/domain/guard.py

"""
Zeno Guard: prop-firm rule enforcement, as pure functions.

A prop account fails the moment equity touches the daily loss limit or the
max drawdown limit. The guard keeps a safety buffer above both, refuses new
trades whose stop would cross the buffer, and flattens the account when
equity falls to it. After a flatten the account stays locked until the next
trading day starts.

Both limits are a percentage of the initial balance, which is how most prop
firms state them. The daily limit is measured from the balance at the start
of the trading day.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

LOCK_FOREVER = "forever"


@dataclass(frozen=True)
class GuardRules:
    daily_loss_pct: float = 5.0
    max_drawdown_pct: float = 10.0
    # Static: the floor stays at initial balance minus the limit.
    # Trailing: the floor follows the highest balance reached.
    trailing_drawdown: bool = False
    # Act this far (in % of initial balance) before a limit is touched.
    buffer_pct: float = 1.0
    max_risk_per_trade_pct: float = 1.0
    max_open_positions: int = 0          # 0 = no limit
    require_stop_loss: bool = True
    # Stop trading once the challenge target is reached; 0 = off.
    profit_target_pct: float = 0.0


@dataclass(frozen=True)
class GuardState:
    """What the guard remembers about one account between checks."""
    initial_balance: float
    day: str                    # trading day, YYYY-MM-DD in the firm's time zone
    day_start_balance: float
    high_balance: float
    locked_until: str = ""      # a trading day, LOCK_FOREVER, or "" when unlocked
    lock_reason: str = ""


@dataclass(frozen=True)
class AccountSnapshot:
    balance: float
    equity: float
    open_positions: int
    # Money lost if every open position with a stop hits it, beyond the
    # floating P&L already in equity. Positions without a stop add nothing.
    open_risk: float = 0.0


@dataclass(frozen=True)
class GuardDecision:
    allow_new: bool
    flatten: bool
    reason: str = ""


def new_state(balance: float, day: str, initial_balance: float | None = None) -> GuardState:
    initial = initial_balance or balance
    return GuardState(initial, day, balance, max(initial, balance))


def roll_day(state: GuardState, day: str, balance: float) -> GuardState:
    """Start a new trading day and track the balance high."""
    state = replace(state, high_balance=max(state.high_balance, balance))
    if day != state.day:
        state = replace(state, day=day, day_start_balance=balance)
        if state.locked_until and state.locked_until != LOCK_FOREVER and day >= state.locked_until:
            state = replace(state, locked_until="", lock_reason="")
    return state


def daily_floor(rules: GuardRules, state: GuardState) -> float:
    return state.day_start_balance - state.initial_balance * rules.daily_loss_pct / 100


def drawdown_floor(rules: GuardRules, state: GuardState) -> float:
    base = state.high_balance if rules.trailing_drawdown else state.initial_balance
    return base - state.initial_balance * rules.max_drawdown_pct / 100


def guard_line(rules: GuardRules, state: GuardState) -> tuple[float, str]:
    """Equity level where the guard acts, and which limit it protects."""
    daily, dd = daily_floor(rules, state), drawdown_floor(rules, state)
    floor, name = (daily, "daily loss limit") if daily >= dd else (dd, "max drawdown")
    return floor + state.initial_balance * rules.buffer_pct / 100, name


def is_locked(state: GuardState) -> bool:
    return bool(state.locked_until)


def lock(state: GuardState, reason: str, next_day: str) -> GuardState:
    until = LOCK_FOREVER if reason == "profit target reached" else next_day
    return replace(state, locked_until=until, lock_reason=reason)


def evaluate(rules: GuardRules, state: GuardState, snap: AccountSnapshot) -> GuardDecision:
    if is_locked(state):
        return GuardDecision(False, snap.open_positions > 0, state.lock_reason)

    line, name = guard_line(rules, state)
    if snap.equity <= line:
        return GuardDecision(False, True, name)

    if rules.profit_target_pct > 0:
        target = state.initial_balance + state.initial_balance * rules.profit_target_pct / 100
        if snap.equity >= target:
            return GuardDecision(False, True, "profit target reached")

    if rules.max_open_positions and snap.open_positions >= rules.max_open_positions:
        return GuardDecision(False, False, "max open positions")

    return GuardDecision(True, False)


def check_order(rules: GuardRules, state: GuardState, snap: AccountSnapshot,
                risk_money: float | None) -> tuple[bool, str]:
    """May a new order with this much money at risk to its stop be sent?"""
    decision = evaluate(rules, state, snap)
    if not decision.allow_new:
        return False, decision.reason

    if risk_money is None:
        if rules.require_stop_loss:
            return False, "no stop loss"
        return True, ""

    max_risk = state.initial_balance * rules.max_risk_per_trade_pct / 100
    if risk_money > max_risk + 1e-9:
        return False, f"risk {risk_money:.2f} above the {rules.max_risk_per_trade_pct:g}% per-trade cap"

    line, name = guard_line(rules, state)
    if snap.equity - snap.open_risk - risk_money <= line:
        return False, f"would breach the {name} buffer if stops are hit"
    return True, ""
