"""CLI execution path: a paper order only becomes a position when it FILLS, the
booked P&L uses the real fill prices, and a failed close leaves the position open.
No network — the broker is a fake that returns canned order results."""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.execution import Executor
from src.broker_cli import BrokerError

ET = ZoneInfo("America/New_York")


class _Cfg:
    def __init__(self, mode):
        self.mode = mode
        self.exits = {"profit_target_pct": 40, "premium_stop_pct": -65,
                      "time_stop_minutes": 30}
        self.risk = {"contracts_per_trade": 1}


class _FakeBroker:
    """Returns queued results for submit_and_fill; records calls."""
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def submit_and_fill(self, occ, side, qty, intent, client_order_id=None,
                        limit_price=None):
        self.calls.append((occ, side, qty, intent))
        return self._results.pop(0)


PREP = {"contract": {"symbol": "SPY260831C00640000", "strike": 640.0,
                     "expiration": "2026-08-31"},
        "quote": {"ask": 1.60, "mid": 1.50, "bid": 1.40},
        "cost_usd": 160.0, "qty": 1}


def _exec(mode, broker=None):
    return Executor(_StubMD(), _Cfg(mode), broker=broker)


class _StubMD:
    def option_quote(self, contract):
        return {"ask": 2.10, "mid": 2.00, "bid": 1.90}


def test_paper_entry_records_fill_and_opens_position():
    broker = _FakeBroker([{"order_id": "o1", "status": "filled",
                           "fill_price": 1.55, "filled_qty": "1"}])
    ex = _exec("paper", broker)
    pos = ex.enter("SPY", "call", PREP)
    assert pos.entry_fill == 1.55
    assert pos.cost_usd == 155.0          # booked from the fill, not the quote
    assert pos.entry_basis == 1.55
    assert len(ex.open_positions) == 1 and ex.trades_today == 1
    assert broker.calls[0][1] == "buy" and broker.calls[0][3] == "buy_to_open"


def test_paper_entry_unfilled_raises_and_books_nothing():
    broker = _FakeBroker([{"order_id": "o2", "status": "rejected",
                           "fill_price": None, "filled_qty": "0"}])
    ex = _exec("paper", broker)
    with pytest.raises(BrokerError):
        ex.enter("SPY", "call", PREP)
    assert ex.open_positions == [] and ex.trades_today == 0


def test_paper_close_uses_fill_vs_fill_pnl():
    broker = _FakeBroker([
        {"order_id": "o1", "status": "filled", "fill_price": 1.00, "filled_qty": "1"},
        {"order_id": "o3", "status": "filled", "fill_price": 1.40, "filled_qty": "1"},
    ])
    ex = _exec("paper", broker)
    pos = ex.enter("SPY", "call", PREP)
    ev = ex._close(pos, {"ask": 1.45, "mid": 1.40, "bid": 1.35}, "profit_target")
    assert ev is not None
    assert ev.exit_fill == 1.40
    assert ev.pnl_usd == pytest.approx((1.40 - 1.00) * 100)   # fill-vs-fill, +$40
    assert pos.open is False
    assert broker.calls[1][1] == "sell" and broker.calls[1][3] == "sell_to_close"


def test_paper_close_unfilled_leaves_position_open():
    broker = _FakeBroker([
        {"order_id": "o1", "status": "filled", "fill_price": 1.00, "filled_qty": "1"},
        {"order_id": "o4", "status": "canceled", "fill_price": None, "filled_qty": "0"},
    ])
    ex = _exec("paper", broker)
    pos = ex.enter("SPY", "call", PREP)
    ev = ex._close(pos, {"ask": 1.45, "mid": 1.40, "bid": 1.35}, "profit_target")
    assert ev is None            # failed close -> no event
    assert pos.open is True      # still held, retried next scan


def test_dry_run_never_touches_broker():
    ex = _exec("dry_run")        # broker stays None
    assert ex.broker is None
    pos = ex.enter("SPY", "call", PREP)
    assert pos.entry_fill is None and pos.entry_basis == 1.60   # modeled ask
    ev = ex._close(pos, {"ask": 2.10, "mid": 2.00, "bid": 1.90}, "profit_target")
    assert ev.exit_fill is None
    assert ev.pnl_usd == pytest.approx((1.90 - 1.60) * 100)     # modeled bid-vs-ask


# The ONLY path that verifies the paper endpoint in the real agent is the
# Executor auto-creating its broker (broker=None, paper). Pin that it happens —
# and that a failed verification blocks startup — so a refactor can't drop it.
class _FakeCLIVerify:
    def __init__(self, *a, verify=None, **k):
        self.verified = 0
        self._verify = verify

    def verify_paper(self):
        self.verified += 1
        if self._verify:
            self._verify()


def test_paper_auto_created_broker_is_verified(monkeypatch):
    import src.execution as ex_mod
    monkeypatch.setattr(ex_mod, "BrokerCLI", _FakeCLIVerify)
    ex = Executor(_StubMD(), _Cfg("paper"), broker=None)   # auto-create path
    assert isinstance(ex.broker, _FakeCLIVerify)
    assert ex.broker.verified == 1


def test_paper_startup_refuses_when_not_paper(monkeypatch):
    import src.execution as ex_mod

    def _boom():
        raise BrokerError("live endpoint")
    monkeypatch.setattr(ex_mod, "BrokerCLI",
                        lambda *a, **k: _FakeCLIVerify(verify=_boom))
    with pytest.raises(BrokerError):
        Executor(_StubMD(), _Cfg("paper"), broker=None)     # guard blocks startup

# ---------------------------------------------------------------------------
# Per-symbol session history that the entry rules read.
# ---------------------------------------------------------------------------
from datetime import timedelta
from src.execution import Position


def _pos(symbol="SPY", direction="call", when=None):
    when = when or datetime.now(ET)
    return Position(symbol=symbol, direction=direction, contract=f"{symbol}_C",
                    strike=1.0, expiration="2026-08-31", qty=1, entry_ask=1.0,
                    entry_mid=1.0, entry_time=when, cost_usd=100.0)


def test_confirmed_close_arms_the_cooldown_clock():
    """last_exit records BOTH time and reason, and is scoped per symbol."""
    ex = Executor(md=None, cfg=_Cfg("dry_run"))
    now = datetime.now(ET)
    pos = _pos(when=now)
    ex.positions.append(pos)
    assert ex.mins_since_last_exit("SPY", now) is None
    assert ex.last_exit_reason("SPY") is None

    ex._close(pos, {"bid": 0.5, "ask": 0.6, "mid": 0.55}, "time_stop")
    assert ex.last_exit_reason("SPY") == "time_stop"
    assert ex.mins_since_last_exit("SPY", now + timedelta(minutes=7)) == pytest.approx(7, abs=0.5)
    assert ex.mins_since_last_exit("QQQ", now) is None      # per symbol, not global


def test_failed_close_does_not_arm_the_cooldown():
    """A sell that never fills must leave the clock untouched, or a broker hiccup
    would silently block re-entry on a position that is still open."""
    broker = _FakeBroker([{"order_id": "o1", "status": "canceled", "fill_price": None}])
    ex = Executor(md=None, cfg=_Cfg("paper"), broker=broker)
    pos = _pos()
    ex.positions.append(pos)
    ev = ex._close(pos, {"bid": 0.5, "ask": 0.6, "mid": 0.55}, "time_stop")
    assert ev is None and pos.open is True
    assert ex.last_exit_reason("SPY") is None
    assert ex.mins_since_last_exit("SPY", datetime.now(ET)) is None


def test_directions_for_is_empty_until_an_entry():
    ex = Executor(md=None, cfg=_Cfg("dry_run"))
    assert ex.directions_for("SPY") == ()
    ex.directions_today.setdefault("SPY", set()).add("call")
    assert ex.directions_for("SPY") == ("call",)
    assert ex.directions_for("QQQ") == ()


def test_holds_open_tracks_the_open_book_per_symbol_direction():
    """The already-holding guard's data source: True only while a matching lot is
    OPEN; a different direction/symbol is unaffected, and a full close clears it."""
    ex = Executor(md=None, cfg=_Cfg("dry_run"))
    assert ex.holds_open("IWM", "put") is False        # empty book
    pos = _pos(symbol="IWM", direction="put")
    ex.positions.append(pos)
    assert ex.holds_open("IWM", "put") is True         # held
    assert ex.holds_open("IWM", "call") is False       # opposite direction
    assert ex.holds_open("SPY", "put") is False        # other symbol
    ex._close(pos, {"bid": 0.5, "ask": 0.6, "mid": 0.55}, "time_stop")
    assert ex.holds_open("IWM", "put") is False        # cleared once fully closed


def test_broker_error_on_close_does_not_arm_the_cooldown():
    """A CLI blow-up (not just an unfilled order) must leave the clock untouched."""
    class _Boom:
        def submit_and_fill(self, *a, **k):
            raise BrokerError("cli exploded")
    ex = Executor(md=None, cfg=_Cfg("paper"), broker=_Boom())
    pos = _pos()
    ex.positions.append(pos)
    assert ex._close(pos, {"bid": 0.5, "ask": 0.6, "mid": 0.55}, "time_stop") is None
    assert pos.open is True
    assert ex.last_exit_reason("SPY") is None
    assert ex.mins_since_last_exit("SPY", datetime.now(ET)) is None


def test_elapsed_minutes_never_goes_negative():
    """A paper close stamps last_exit after the poll, which can be later than the
    scan's `now`; the rule still blocks but the log must not print '-1m'."""
    from datetime import timedelta
    ex = Executor(md=None, cfg=_Cfg("dry_run"))
    now = datetime.now(ET)
    pos = _pos(when=now)
    ex.positions.append(pos)
    ex._close(pos, {"bid": 0.5, "ask": 0.6, "mid": 0.55}, "time_stop")
    assert ex.mins_since_last_exit("SPY", now - timedelta(minutes=1)) == 0.0


# ---------------------------------------------------------------------------
# The two-tier scale-out LADDER exit (exit_mode="ladder"). Driven in
# dry_run so no broker is needed; a mutable MD lets each manage() see a new mid.
# ---------------------------------------------------------------------------
from src.execution import Position


class _LadderCfg:
    def __init__(self):
        self.mode = "dry_run"
        self.exits = {"exit_mode": "ladder", "tier1_target_pct": 20,
                      "tier1_trail_price_drop_pct": 10, "tier2_target_pct": 40,
                      "runner_giveback_pct": 40, "premium_stop_pct": -65,
                      "time_stop_minutes": 30}
        self.risk = {"contracts_per_trade": 50}


class _MutMD:
    def __init__(self, mid):
        self.mid = mid

    def option_quote(self, contract):
        m = self.mid
        return {"ask": round(m * 1.02, 2), "mid": m, "bid": round(m * 0.98, 2)}


def _lpos(qty=50, entry_mid=1.00, when=None):
    when = when or datetime.now(ET)
    return Position(symbol="SPY", direction="call", contract="SPY_C", strike=1.0,
                    expiration="2026-08-31", qty=qty, entry_ask=entry_mid,
                    entry_mid=entry_mid, entry_time=when,
                    cost_usd=entry_mid * 100 * qty, opened_qty=qty)


def _lex(mid):
    return Executor(_MutMD(mid), _LadderCfg())


def test_ladder_tier1_sells_half_at_the_20pct_target():
    md = _MutMD(1.00)
    ex = Executor(md, _LadderCfg())
    pos = _lpos(); ex.positions.append(pos)
    md.mid = 1.15
    assert ex.manage(datetime.now(ET)) == []            # below +20%: hold
    assert pos.qty == 50 and pos.scale_count == 0
    md.mid = 1.20                                        # +20%: sell HALF
    evs = ex.manage(datetime.now(ET))
    assert len(evs) == 1 and evs[0].reason == "profit_target" and evs[0].qty == 25
    assert pos.qty == 25 and pos.scale_count == 1 and pos.scaled is True


def test_ladder_tier1_runner_trails_on_10pct_price_drop_from_peak():
    md = _MutMD(1.00)
    ex = Executor(md, _LadderCfg())
    pos = _lpos(); ex.positions.append(pos)
    md.mid = 1.20; ex.manage(datetime.now(ET))          # tier1 -> 25 left
    md.mid = 1.30                                        # climbs: new peak, no exit
    assert ex.manage(datetime.now(ET)) == []
    assert pos.high_water_price == 1.30 and pos.qty == 25
    md.mid = 1.18                                        # -9.2% from 1.30: still holds
    assert ex.manage(datetime.now(ET)) == []
    md.mid = 1.16                                        # -10.8% from peak 1.30: exit runner
    evs = ex.manage(datetime.now(ET))
    assert len(evs) == 1 and evs[0].reason == "runner_trail"
    assert pos.qty == 0 and pos.open is False


def test_ladder_tier2_sells_another_half_then_gain_giveback_trail():
    md = _MutMD(1.00)
    ex = Executor(md, _LadderCfg())
    pos = _lpos(); ex.positions.append(pos)
    md.mid = 1.20; ex.manage(datetime.now(ET))          # tier1: 25 left, sc=1
    md.mid = 1.40                                        # +40%: tier2 sells half of 25
    evs = ex.manage(datetime.now(ET))
    assert len(evs) == 1 and evs[0].reason == "profit_target_2" and evs[0].qty == 12
    assert pos.qty == 13 and pos.scale_count == 2
    md.mid = 2.00                                        # peak gain 100%; floor = 60%
    assert ex.manage(datetime.now(ET)) == []
    md.mid = 1.55                                        # +55% <= 60% floor: trail out
    evs = ex.manage(datetime.now(ET))
    assert len(evs) == 1 and evs[0].reason == "runner_trail" and pos.qty == 0


def test_ladder_premium_stop_before_tier1():
    md = _MutMD(1.00)
    ex = Executor(md, _LadderCfg())
    pos = _lpos(); ex.positions.append(pos)
    md.mid = 0.30                                        # -70% <= -65%
    evs = ex.manage(datetime.now(ET))
    assert len(evs) == 1 and evs[0].reason == "premium_stop" and pos.qty == 0


def test_ladder_time_stop_before_tier1_when_red_and_stale():
    from datetime import timedelta
    when = datetime.now(ET) - timedelta(minutes=31)
    md = _MutMD(1.00)
    ex = Executor(md, _LadderCfg())
    pos = _lpos(when=when); ex.positions.append(pos)
    md.mid = 0.95                                        # -5%, >30 min old
    evs = ex.manage(datetime.now(ET))
    assert len(evs) == 1 and evs[0].reason == "time_stop" and pos.qty == 0


def test_ladder_one_lot_closes_outright_at_tier1():
    md = _MutMD(1.00)
    ex = Executor(md, _LadderCfg())
    pos = _lpos(qty=1); ex.positions.append(pos)
    md.mid = 1.25                                        # +25%: no half to sell
    evs = ex.manage(datetime.now(ET))
    assert len(evs) == 1 and evs[0].reason == "profit_target"
    assert pos.qty == 0 and pos.scale_count == 0 and pos.open is False


def test_arm_a_default_still_uses_single_scale_engine():
    """No exit_mode => the single-scale engine, unchanged."""
    md = _MutMD(1.00)
    cfg = _Cfg("dry_run")
    cfg.exits = {"profit_target_pct": 40, "premium_stop_pct": -65,
                 "time_stop_minutes": 30, "scale_out_at_target": True,
                 "runner_giveback_pct": 40}
    ex = Executor(md, cfg)
    pos = _lpos(); ex.positions.append(pos)
    md.mid = 1.20                                        # +20% < the 40% target
    assert ex.manage(datetime.now(ET)) == []            # ladder would have scaled here
    assert pos.qty == 50 and pos.scale_count == 0


# ---------------------------------------------------------------------------
# Stop-loss ladder (adopted 2026-09-03): -20% sell half, -40% rest,
# -65% premium_stop kept as a deeper backstop. Shared by BOTH exit engines.
# ---------------------------------------------------------------------------
def _stop_ladder_exits(base):
    d = dict(base)
    d.update({"stop_ladder_enabled": True, "stop1_loss_pct": -20, "stop2_loss_pct": -40})
    return d


def _ladder_stop_ex(mid):
    md = _MutMD(mid)
    cfg = _LadderCfg()
    cfg.exits = _stop_ladder_exits(cfg.exits)
    return md, Executor(md, cfg)


def test_stop_ladder_sells_half_at_minus20_then_rest_at_minus40():
    md, ex = _ladder_stop_ex(1.00)
    pos = _lpos(qty=50); ex.positions.append(pos)
    md.mid = 0.85                                        # -15%: above stop1, hold
    assert ex.manage(datetime.now(ET)) == []
    assert pos.qty == 50 and pos.loss_scale_count == 0
    md.mid = 0.80                                        # -20%: sell HALF
    evs = ex.manage(datetime.now(ET))
    assert len(evs) == 1 and evs[0].reason == "stop_loss_1" and evs[0].qty == 25
    assert pos.qty == 25 and pos.loss_scale_count == 1 and pos.open is True
    md.mid = 0.82                                        # bounce to -18%: no re-fire
    assert ex.manage(datetime.now(ET)) == []
    assert pos.qty == 25
    md.mid = 0.60                                        # -40%: sell the REST
    evs = ex.manage(datetime.now(ET))
    assert len(evs) == 1 and evs[0].reason == "stop_loss_2"
    assert pos.qty == 0 and pos.open is False


def test_stop_ladder_gap_past_minus40_closes_all_at_once():
    md, ex = _ladder_stop_ex(1.00)
    pos = _lpos(qty=50); ex.positions.append(pos)
    md.mid = 0.55                                        # gaps to -45%: stop_loss_2, all
    evs = ex.manage(datetime.now(ET))
    assert len(evs) == 1 and evs[0].reason == "stop_loss_2"
    assert pos.qty == 0 and pos.loss_scale_count == 0 and pos.open is False


def test_stop_ladder_fires_before_the_minus65_backstop():
    """-40% closes the rest, so premium_stop(-65) is only a gap backstop."""
    md, ex = _ladder_stop_ex(1.00)
    pos = _lpos(qty=50); ex.positions.append(pos)
    md.mid = 0.30                                        # -70%: stop_loss_2 wins (checked first)
    evs = ex.manage(datetime.now(ET))
    assert len(evs) == 1 and evs[0].reason == "stop_loss_2"
    assert pos.qty == 0


def test_stop_ladder_disabled_by_default_leaves_engine_unchanged():
    md = _MutMD(1.00)
    ex = Executor(md, _LadderCfg())                      # no stop_ladder_enabled
    pos = _lpos(qty=50); ex.positions.append(pos)
    md.mid = 0.80                                        # -20%: nothing without the ladder
    assert ex.manage(datetime.now(ET)) == []
    assert pos.qty == 50 and pos.loss_scale_count == 0


def test_stop_ladder_works_in_scale_single_engine_too():
    """scale_single also gets the -20/-40 stop ladder."""
    md = _MutMD(1.00)
    cfg = _Cfg("dry_run")
    cfg.exits = _stop_ladder_exits({"profit_target_pct": 40, "premium_stop_pct": -65,
                                    "time_stop_minutes": 30, "scale_out_at_target": True,
                                    "runner_giveback_pct": 40})
    ex = Executor(md, cfg)
    pos = _lpos(qty=50); ex.positions.append(pos)
    md.mid = 0.80                                        # -20%: sell half
    evs = ex.manage(datetime.now(ET))
    assert len(evs) == 1 and evs[0].reason == "stop_loss_1" and pos.qty == 25
    md.mid = 0.60                                        # -40%: rest
    evs = ex.manage(datetime.now(ET))
    assert len(evs) == 1 and evs[0].reason == "stop_loss_2" and pos.qty == 0


# ---------------------------------------------------------------------------
# Regression: the -20% half-cut must NOT turn the remainder into a runner.
# Setting pos.scaled routes a position into the runner branch, which trails a
# high-water mark and never consults _exit_reason. That is right after a +40%
# profit scale-out and wrong after a loss cut: it left the surviving half with
# no time stop and no profit target until -40% or the EOD flatten.
# ---------------------------------------------------------------------------
def _armA_stop_ex(mid):
    """The default engine (scale_single, no exit_mode) WITH the -20/-40 stop ladder."""
    md = _MutMD(mid)
    cfg = _Cfg("dry_run")
    cfg.exits = _stop_ladder_exits({"profit_target_pct": 40, "premium_stop_pct": -65,
                                    "time_stop_minutes": 30, "scale_out_at_target": True,
                                    "runner_giveback_pct": 40})
    return md, Executor(md, cfg)


def test_loss_cut_does_not_mark_the_remainder_a_runner():
    md, ex = _armA_stop_ex(1.00)
    pos = _lpos(qty=50); ex.positions.append(pos)
    md.mid = 0.78                                        # -22%: sell HALF
    evs = ex.manage(datetime.now(ET))
    assert len(evs) == 1 and evs[0].reason == "stop_loss_1"
    assert pos.qty == 25 and pos.loss_scale_count == 1
    assert pos.scaled is False, "a loss cut must not claim the remainder is a runner"


def test_time_stop_still_fires_on_the_half_left_after_a_loss_cut():
    md, ex = _armA_stop_ex(1.00)
    opened = datetime.now(ET) - timedelta(minutes=45)
    pos = _lpos(qty=50); pos.entry_time = opened; ex.positions.append(pos)
    md.mid = 0.78                                        # -22%: half goes
    assert ex.manage(opened + timedelta(minutes=5))[0].reason == "stop_loss_1"
    assert pos.qty == 25 and pos.open is True
    # 35 minutes in, still under water but above -40%: the time stop owns it.
    md.mid = 0.85                                        # -15%
    evs = ex.manage(opened + timedelta(minutes=35))
    assert len(evs) == 1 and evs[0].reason == "time_stop", evs
    assert pos.qty == 0 and pos.open is False


def test_profit_target_still_reachable_after_a_loss_cut():
    """Cut at -20%, then recovers: the surviving half must still scale at +40%."""
    md, ex = _armA_stop_ex(1.00)
    pos = _lpos(qty=50); ex.positions.append(pos)
    md.mid = 0.78
    assert ex.manage(datetime.now(ET))[0].reason == "stop_loss_1"
    md.mid = 1.45                                        # +45% on the surviving half
    evs = ex.manage(datetime.now(ET))
    assert len(evs) == 1 and evs[0].reason == "profit_target", evs
