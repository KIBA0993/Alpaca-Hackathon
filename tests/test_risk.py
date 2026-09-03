"""Risk-manager tests — defined-risk caps and session windows."""
import sys
from datetime import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.risk import RiskManager, RiskState

CFG = {"contracts_per_trade": 1, "max_concurrent_positions": 3,
       "max_trades_per_day": 6, "max_premium_per_trade_usd": 200,
       "no_entry_after": "15:00", "eod_flatten": "15:50"}


def rm():
    return RiskManager(CFG)


def test_ok():
    ok, why = rm().check_entry(RiskState(time(10, 0), 0, 0, 150))
    assert ok and why == "ok"


def test_after_cutoff():
    ok, why = rm().check_entry(RiskState(time(15, 1), 0, 0, 150))
    assert not ok and "cutoff" in why


def test_max_positions():
    ok, why = rm().check_entry(RiskState(time(10, 0), 3, 0, 150))
    assert not ok and "concurrent" in why


def test_max_trades():
    ok, why = rm().check_entry(RiskState(time(10, 0), 0, 6, 150))
    assert not ok and "per day" in why


def test_premium_cap():
    ok, why = rm().check_entry(RiskState(time(10, 0), 0, 0, 250))
    assert not ok and "cap" in why


def test_flatten():
    assert rm().should_flatten(time(15, 50))
    assert not rm().should_flatten(time(15, 49))
