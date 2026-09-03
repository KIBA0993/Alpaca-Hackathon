"""Adaptive sizing + scale-out/runner + partial fills + broker sweep. No network."""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.execution import Executor, Position

ET = ZoneInfo("America/New_York")


class _Cfg:
    def __init__(self, mode="dry_run", exits=None, risk=None):
        self.mode = mode
        self.exits = {"profit_target_pct": 40, "premium_stop_pct": -65,
                      "time_stop_minutes": 30, "scale_out_at_target": True,
                      "runner_giveback_pct": 40, **(exits or {})}
        self.risk = {"contracts_per_trade": 50, "max_premium_per_trade_usd": 35000,
                     "max_open_premium_usd": 95000, "entry_limit_slippage_pct": 2.0,
                     "flatten_foreign_at_startup": False, **(risk or {})}


class _MD:
    """option_quote walks a scripted quote path (one per manage scan)."""
    def __init__(self, quotes=None):
        self._q = list(quotes or [])
        self.i = 0

    def option_quote(self, contract):
        if not self._q:
            return {"bid": 1.0, "ask": 1.0, "mid": 1.0}
        q = self._q[min(self.i, len(self._q) - 1)]
        self.i += 1
        return q

    def atm_contract(self, symbol, direction, spot=None):
        return {"symbol": "SPY260831C00640000", "strike": 640.0,
                "expiration": "2026-08-31"}


def _q(mid):
    return {"bid": round(mid - 0.01, 2), "ask": round(mid + 0.01, 2), "mid": mid}


def _pos(mid=1.00, ask=1.00, qty=10):
    return Position(symbol="SPY", direction="call", contract="SPY_C", strike=1.0,
                    expiration="2026-08-31", qty=qty, entry_ask=ask, entry_mid=mid,
                    entry_time=datetime.now(ET), cost_usd=ask * 100 * qty, opened_qty=qty)


# ------------------------------------------------------------------ sizing
def test_adaptive_size_hits_target_when_cheap():
    ex = Executor(_MD(), _Cfg("dry_run"))
    assert ex.size_for(ask=2.00, open_premium=0) == 50           # 35000//200 capped at 50


def test_adaptive_size_trims_expensive_contract_instead_of_rejecting():
    ex = Executor(_MD(), _Cfg("dry_run"))
    assert ex.size_for(ask=14.60, open_premium=0) == 23          # 35000//1460, not a reject


def test_adaptive_size_respects_remaining_room():
    ex = Executor(_MD(), _Cfg("dry_run"))
    assert ex.size_for(ask=2.00, open_premium=94000) == 5        # room 1000 -> 5 lots
    assert ex.size_for(ask=2.00, open_premium=95000) == 0        # no room -> can't afford one


# --------------------------------------------------------- scale-out + runner
def test_profit_target_scales_out_half_and_keeps_a_runner():
    ex = Executor(_MD([_q(1.50)]), _Cfg("dry_run"))
    pos = _pos(mid=1.00, ask=1.00, qty=10)
    ex.positions.append(pos)
    events = ex.manage(datetime.now(ET))
    assert len(events) == 1 and events[0].reason == "profit_target"
    assert events[0].qty == 5                     # sold half of 10
    assert pos.qty == 5 and pos.scaled is True and pos.open is True   # runner remains
    assert pos.high_water_pct == pytest.approx(50, abs=0.5)
    assert ex.last_exit_reason("SPY") is None      # a scale-out does NOT arm the cooldown


def test_runner_trails_on_giveback_of_peak_gain():
    # +40% (scale, hw=40) -> +60% (hw=60) -> +30% (<= 60*0.6=36 floor) -> runner_trail
    ex = Executor(_MD([_q(1.50), _q(1.70), _q(1.35)]), _Cfg("dry_run"))
    pos = _pos(mid=1.00, ask=1.00, qty=10)
    ex.positions.append(pos)
    ex.manage(datetime.now(ET))                   # scale
    ex.manage(datetime.now(ET))                   # hold (60% peak)
    evs = ex.manage(datetime.now(ET))             # give back to 30% -> trail
    assert len(evs) == 1 and evs[0].reason == "runner_trail"
    assert evs[0].qty == 5 and pos.qty == 0 and pos.open is False
    assert ex.last_exit_reason("SPY") == "runner_trail"   # full close DOES arm


def test_premium_stop_sells_whole_runner_not_half():
    ex = Executor(_MD([_q(1.50), _q(0.30)]), _Cfg("dry_run"))   # scale, then -70%
    pos = _pos(mid=1.00, ask=1.00, qty=10)
    ex.positions.append(pos)
    ex.manage(datetime.now(ET))                   # scale -> runner of 5
    evs = ex.manage(datetime.now(ET))             # -70% -> premium_stop sells ALL 5
    assert evs[0].reason == "premium_stop" and evs[0].qty == 5 and pos.qty == 0


def test_one_lot_closes_fully_at_target_no_runner():
    ex = Executor(_MD([_q(1.50)]), _Cfg("dry_run"))
    pos = _pos(mid=1.00, ask=1.00, qty=1)
    ex.positions.append(pos)
    evs = ex.manage(datetime.now(ET))
    assert evs[0].reason == "profit_target" and evs[0].qty == 1
    assert pos.qty == 0 and pos.open is False and pos.scaled is False


def test_scale_out_disabled_flag_closes_fully():
    ex = Executor(_MD([_q(1.50)]), _Cfg("dry_run", exits={"scale_out_at_target": False}))
    pos = _pos(mid=1.00, ask=1.00, qty=10)
    ex.positions.append(pos)
    evs = ex.manage(datetime.now(ET))
    assert evs[0].reason == "profit_target" and evs[0].qty == 10 and pos.qty == 0


# ------------------------------------------------------------- partial fills
class _Broker:
    def __init__(self, results, positions=None, bp=None):
        self._r = list(results)
        self.calls = []
        self._positions = positions
        self._bp = bp

    def submit_and_fill(self, occ, side, qty, intent, client_order_id=None,
                        limit_price=None):
        self.calls.append((occ, side, qty, intent))
        return self._r.pop(0)

    def list_option_positions(self):
        return dict(self._positions or {})

    def options_buying_power(self):
        return self._bp


def test_partial_buy_opens_at_actual_filled_qty():
    br = _Broker([{"order_id": "b1", "status": "filled", "fill_price": 2.00,
                   "filled_qty": 30}])
    ex = Executor(_MD(), _Cfg("paper"), broker=br)
    prep = {"contract": {"symbol": "SPY_C", "strike": 1.0, "expiration": "x"},
            "quote": {"ask": 2.00, "mid": 1.95, "bid": 1.90}, "cost_usd": 10000.0, "qty": 50}
    pos = ex.enter("SPY", "call", prep)
    assert pos.qty == 30 and pos.opened_qty == 30                 # booked the partial
    assert pos.cost_usd == pytest.approx(2.00 * 100 * 30)


def test_partial_sell_decrements_and_leaves_open():
    br = _Broker([
        {"order_id": "b1", "status": "filled", "fill_price": 2.00, "filled_qty": 30},
        {"order_id": "s1", "status": "canceled", "fill_price": 2.50, "filled_qty": 20},
    ])
    ex = Executor(_MD(), _Cfg("paper", exits={"scale_out_at_target": False}), broker=br)
    prep = {"contract": {"symbol": "SPY_C", "strike": 1.0, "expiration": "x"},
            "quote": {"ask": 2.00, "mid": 1.95, "bid": 1.90}, "cost_usd": 10000.0, "qty": 50}
    pos = ex.enter("SPY", "call", prep)           # opens 30
    ev = ex._close(pos, {"ask": 2.55, "mid": 2.50, "bid": 2.45}, "premium_stop")
    assert ev is not None and ev.qty == 20        # only 20 of 30 filled
    assert pos.qty == 10 and pos.open is True      # 10 remain, still open
    assert ex.last_exit_reason("SPY") is None      # not fully closed -> no cooldown armed


# --------------------------------------------------------------- broker sweep
def test_startup_flattens_foreign_option_position():
    br = _Broker([{"order_id": "x", "status": "filled", "fill_price": 1.0, "filled_qty": 7}],
                 positions={"QQQ260831C00500000": 7})
    Executor(_MD(), _Cfg("paper", risk={"flatten_foreign_at_startup": True}), broker=br)
    assert br.calls and br.calls[0][1] == "sell" and br.calls[0][2] == 7


def test_eod_sweep_flattens_broker_stragglers():
    br = _Broker([{"order_id": "x", "status": "filled", "fill_price": 1.0, "filled_qty": 3}],
                 positions={"SPY260831P00600000": 3})
    ex = Executor(_MD(), _Cfg("paper"), broker=br)   # flatten_foreign off -> no startup sweep
    swept = ex.flatten_broker_stragglers()
    assert len(swept) == 1 and swept[0]["qty"] == 3 and br.calls[0][1] == "sell"


def test_aggregate_cap_is_capped_by_real_buying_power():
    # config says $95k, but the account only has $6k options BP -> sizing must use $6k.
    br = _Broker([], bp=6000.0)
    ex = Executor(_MD(), _Cfg("paper"), broker=br)
    assert ex.max_open_premium == 6000.0
    assert ex.size_for(ask=2.00, open_premium=0) == 30           # 6000//200, not 50


def test_entry_adopts_late_filled_remainder():
    # booked 30 of 50, but the broker actually holds 50 (the cancel lost the race).
    br = _Broker([{"order_id": "b1", "status": "canceled", "fill_price": 2.00,
                   "filled_qty": 30}],
                 positions={"SPY_C": 50})
    ex = Executor(_MD(), _Cfg("paper"), broker=br)
    prep = {"contract": {"symbol": "SPY_C", "strike": 1.0, "expiration": "x"},
            "quote": {"ask": 2.00, "mid": 1.95, "bid": 1.90}, "cost_usd": 10000.0, "qty": 50}
    pos = ex.enter("SPY", "call", prep)
    assert pos.qty == 50 and pos.opened_qty == 50                 # adopted the remainder
    assert pos.cost_usd == pytest.approx(2.00 * 100 * 50)
