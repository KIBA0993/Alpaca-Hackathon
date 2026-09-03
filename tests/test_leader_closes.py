"""marketdata.leader_closes — the no-lookahead contract, tested without a network.

This is the function the whole T6 filter rests on: if it ever returns today's
partial daily bar, the regime is computed from the future. The date filter is
pinned here with an injected fake client rather than against the live API.
"""
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.marketdata import MarketData


class _FakeBars:
    def __init__(self, df):
        self.df = df


class _FakeStock:
    """Returns a canned frame and records how it was asked."""
    def __init__(self, df):
        self._df = df
        self.requests = []

    def get_stock_bars(self, req):
        self.requests.append(req)
        return _FakeBars(self._df)


def _frame(rows):
    """rows = [(symbol, 'YYYY-MM-DD', close)] -> Alpaca-shaped daily frame (UTC)."""
    return pd.DataFrame({
        "symbol": [r[0] for r in rows],
        # Alpaca stamps a daily bar at 04:00Z (EDT) / 05:00Z (EST) on the session date
        "timestamp": pd.to_datetime([f"{r[1]} 04:00:00+00:00" for r in rows], utc=True),
        "close": [float(r[2]) for r in rows],
    })


def _md(df):
    md = MarketData.__new__(MarketData)          # skip client construction
    md.stock = _FakeStock(df)
    md._5m_cache = {}
    md._leader_cache = {}
    return md


def test_todays_bar_is_excluded():
    """THE lookahead guard: a bar dated `day` must never be returned."""
    md = _md(_frame([("NVDA", "2026-08-27", 10), ("NVDA", "2026-08-28", 20),
                     ("NVDA", "2026-08-31", 99)]))
    got = md.leader_closes(["NVDA"], day=date(2026, 8, 31))
    assert got["NVDA"] == [10.0, 20.0]           # 99 is TODAY -> dropped
    assert 99.0 not in got["NVDA"]


def test_the_same_bar_becomes_visible_the_next_session():
    df = _frame([("NVDA", "2026-08-28", 20), ("NVDA", "2026-08-31", 99)])
    assert _md(df).leader_closes(["NVDA"], day=date(2026, 8, 31))["NVDA"] == [20.0]
    assert _md(df).leader_closes(["NVDA"], day=date(2026, 9, 1))["NVDA"] == [20.0, 99.0]


def test_future_bars_are_excluded_too():
    md = _md(_frame([("NVDA", "2026-08-27", 10), ("NVDA", "2026-09-15", 77)]))
    assert md.leader_closes(["NVDA"], day=date(2026, 8, 31))["NVDA"] == [10.0]


def test_est_winter_stamp_lands_on_the_right_session_date():
    """In EST Alpaca stamps 05:00Z; it must still resolve to that session's date."""
    df = pd.DataFrame({
        "symbol": ["NVDA", "NVDA"],
        "timestamp": pd.to_datetime(["2026-01-05 05:00:00+00:00",
                                     "2026-01-06 05:00:00+00:00"], utc=True),
        "close": [10.0, 99.0],
    })
    assert _md(df).leader_closes(["NVDA"], day=date(2026, 1, 6))["NVDA"] == [10.0]


def test_closes_are_ascending_by_date():
    md = _md(_frame([("NVDA", "2026-08-28", 30), ("NVDA", "2026-08-26", 10),
                     ("NVDA", "2026-08-27", 20)]))
    assert md.leader_closes(["NVDA"], day=date(2026, 8, 31))["NVDA"] == [10.0, 20.0, 30.0]


def test_multiple_symbols_are_split_correctly():
    md = _md(_frame([("NVDA", "2026-08-27", 1), ("MSFT", "2026-08-27", 2),
                     ("NVDA", "2026-08-28", 3), ("MSFT", "2026-08-28", 4)]))
    got = md.leader_closes(["NVDA", "MSFT"], day=date(2026, 8, 31))
    assert got["NVDA"] == [1.0, 3.0] and got["MSFT"] == [2.0, 4.0]


def test_empty_and_failing_responses_fail_closed():
    assert _md(pd.DataFrame()).leader_closes(["NVDA"], day=date(2026, 8, 31)) == {}

    class _Boom:
        def get_stock_bars(self, req):
            raise RuntimeError("alpaca down")
    md = MarketData.__new__(MarketData)
    md.stock = _Boom(); md._5m_cache = {}; md._leader_cache = {}
    assert md.leader_closes(["NVDA"], day=date(2026, 8, 31)) == {}


def test_result_is_cached_per_day():
    md = _md(_frame([("NVDA", "2026-08-27", 10)]))
    md.leader_closes(["NVDA"], day=date(2026, 8, 31))
    md.leader_closes(["NVDA"], day=date(2026, 8, 31))
    assert len(md.stock.requests) == 1              # one fetch, not two
    md.leader_closes(["NVDA"], day=date(2026, 9, 1))
    assert len(md.stock.requests) == 2              # new day -> new fetch


def test_longer_sma_widens_the_fetch_window():
    """leader_sma_days must drive the lookback or a long SMA silently starves."""
    md = _md(_frame([("NVDA", "2026-08-27", 10)]))
    md.leader_closes(["NVDA"], day=date(2026, 8, 31), sma_days=20)
    md.leader_closes(["NVDA"], day=date(2026, 9, 1), sma_days=200)
    short, long_ = md.stock.requests[0].start, md.stock.requests[1].start
    assert long_ < short
