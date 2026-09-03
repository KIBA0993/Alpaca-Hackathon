"""Arm-C entry rules: one-direction-per-underlying + the SCOPED cooldown."""
from datetime import time
import pytest
from src.risk import RiskManager, RiskState

ENTRY = {"one_direction_per_underlying": True,
         "entry_cooldown_minutes": 15,
         "entry_cooldown_after_exit_reasons": ["time_stop"]}


def mk(**kw):
    base = dict(now=time(10, 0), open_positions=0, trades_today=0,
                premium_cost_usd=100.0, symbol="SPY", direction="call")
    base.update(kw)
    return RiskState(**base)


def rm(entry=None, risk=None):
    return RiskManager(risk or {"no_entry_after": "15:00"}, entry or ENTRY)


# ------------------------------------------------ one direction per underlying
def test_opposite_direction_refused_after_a_trade():
    ok, why = rm().check_entry(mk(direction="put", directions_today=("call",)))
    assert not ok and "one_direction_per_underlying" in why


def test_same_direction_still_allowed():
    assert rm().check_entry(mk(direction="call", directions_today=("call",)))[0]


def test_other_symbol_is_unaffected():
    """The rule is per underlying; QQQ must not inherit SPY's direction."""
    assert rm().check_entry(mk(symbol="QQQ", direction="put", directions_today=()))[0]


def test_rule_off_allows_the_flip():
    r = rm({**ENTRY, "one_direction_per_underlying": False})
    assert r.check_entry(mk(direction="put", directions_today=("call",)))[0]


# ------------------------------------------------------------ scoped cooldown
def test_blocks_inside_window_after_time_stop():
    ok, why = rm().check_entry(mk(mins_since_last_exit=5, last_exit_reason="time_stop"))
    assert not ok and "cooldown" in why and "time_stop" in why


def test_allows_after_a_different_exit_reason():
    """THE POINT OF THE SCOPE: a runner_trail close must not block re-entry."""
    for reason in ("runner_trail", "profit_target", "premium_stop", "eod_flatten"):
        assert rm().check_entry(mk(mins_since_last_exit=1, last_exit_reason=reason))[0], reason


def test_allows_once_the_window_has_passed():
    assert rm().check_entry(mk(mins_since_last_exit=15.0, last_exit_reason="time_stop"))[0]
    assert rm().check_entry(mk(mins_since_last_exit=99.0, last_exit_reason="time_stop"))[0]


def test_boundary_is_exclusive_at_exactly_the_window():
    assert not rm().check_entry(mk(mins_since_last_exit=14.99, last_exit_reason="time_stop"))[0]
    assert rm().check_entry(mk(mins_since_last_exit=15.0, last_exit_reason="time_stop"))[0]


def test_no_prior_exit_never_blocks():
    assert rm().check_entry(mk(mins_since_last_exit=None, last_exit_reason=None))[0]


def test_empty_reason_list_means_blanket_cooldown():
    """Arm C's shipped rule must remain reachable by config alone."""
    r = rm({**ENTRY, "entry_cooldown_after_exit_reasons": [], "entry_cooldown_minutes": 30})
    assert not r.check_entry(mk(mins_since_last_exit=5, last_exit_reason="runner_trail"))[0]
    assert not r.check_entry(mk(mins_since_last_exit=5, last_exit_reason="time_stop"))[0]


def test_zero_minutes_disables_the_cooldown():
    r = rm({**ENTRY, "entry_cooldown_minutes": 0})
    assert r.check_entry(mk(mins_since_last_exit=0.0, last_exit_reason="time_stop"))[0]


# ------------------------------------------------- entry-anchored dedup (Q2 fix)
ENTRY_DD = {**ENTRY, "dedup_minutes": 30}


def test_dedup_blocks_a_repeat_entry_inside_the_window():
    """The gap the exit-cooldown can't cover: re-entering the SAME symbol+
    direction every scan while nothing has closed (live IWM stack 2026-08-31)."""
    ok, why = rm(ENTRY_DD).check_entry(mk(direction="put", mins_since_last_entry=5))
    assert not ok and "dedup" in why


def test_dedup_allows_once_the_window_has_passed():
    assert rm(ENTRY_DD).check_entry(mk(mins_since_last_entry=30.0))[0]
    assert rm(ENTRY_DD).check_entry(mk(mins_since_last_entry=90.0))[0]


def test_dedup_boundary_is_exclusive_at_exactly_the_window():
    assert not rm(ENTRY_DD).check_entry(mk(mins_since_last_entry=29.99))[0]
    assert rm(ENTRY_DD).check_entry(mk(mins_since_last_entry=30.0))[0]


def test_dedup_no_prior_entry_never_blocks():
    assert rm(ENTRY_DD).check_entry(mk(mins_since_last_entry=None))[0]


def test_dedup_zero_or_absent_disables_the_gate():
    assert rm({**ENTRY, "dedup_minutes": 0}).check_entry(mk(mins_since_last_entry=0.0))[0]
    # ENTRY (no dedup_minutes key at all) must not throttle
    assert rm(ENTRY).check_entry(mk(mins_since_last_entry=0.0))[0]


def test_dedup_is_reported_before_the_global_caps():
    ok, why = rm(ENTRY_DD).check_entry(mk(trades_today=99, mins_since_last_entry=1))
    assert not ok and "dedup" in why


# ------------------------------------------- already-holding guard (arm C parity)
def test_already_holding_blocks_a_second_lot_same_symbol_direction():
    """Arm C's _held_symbol_directions guard: never stack a second open lot on a
    pair already held. This is the case dedup misses — a lot still open after the
    30-min window (live IWM 10:37 then 11:07 on 2026-08-31)."""
    ok, why = rm(ENTRY_DD).check_entry(
        mk(direction="put", already_holding=True, mins_since_last_entry=45))
    assert not ok and "already holding" in why


def test_already_holding_is_reported_before_dedup_and_caps():
    """Held-and-throttled must read as 'already holding', not a stale timer or a
    max-trades cap."""
    ok, why = rm(ENTRY_DD).check_entry(
        mk(direction="put", already_holding=True,
           mins_since_last_entry=5, trades_today=99))
    assert not ok and "already holding" in why


def test_not_holding_is_inert():
    assert rm(ENTRY_DD).check_entry(mk(direction="put", already_holding=False))[0]


def test_already_holding_guard_needs_no_config_key():
    """Unconditional, like the arms — it fires even with an empty entry config."""
    r = RiskManager({"no_entry_after": "15:00"})
    ok, why = r.check_entry(mk(direction="put", already_holding=True))
    assert not ok and "already holding" in why


# -------------------------------------------------------------- interactions
def test_entry_rules_run_before_the_global_caps():
    """A cooldown block should be reported as a cooldown, not as 'max trades'."""
    r = rm()
    ok, why = r.check_entry(mk(trades_today=99, mins_since_last_exit=1,
                               last_exit_reason="time_stop"))
    assert not ok and "cooldown" in why


def test_global_caps_still_apply_when_entry_rules_pass():
    r = rm(risk={"no_entry_after": "15:00", "max_trades_per_day": 6})
    ok, why = r.check_entry(mk(trades_today=99))
    assert not ok and "max trades per day" in why


def test_daily_trade_cap_is_off_by_default_and_at_zero():
    """max_trades_per_day removed (arm C parity): absent or 0 => no daily count
    cap, so a high trades_today does NOT block on its own."""
    assert rm().check_entry(mk(trades_today=999))[0]          # absent -> uncapped
    r = rm(risk={"no_entry_after": "15:00", "max_trades_per_day": 0})
    assert r.check_entry(mk(trades_today=999))[0]             # explicit 0 -> uncapped


def test_defaults_are_inert_when_no_entry_config_given():
    """An unconfigured RiskManager must behave exactly as it did before."""
    r = RiskManager({"no_entry_after": "15:00"})
    assert r.check_entry(mk(direction="put", directions_today=("call",),
                            mins_since_last_exit=0.0, last_exit_reason="time_stop"))[0]
