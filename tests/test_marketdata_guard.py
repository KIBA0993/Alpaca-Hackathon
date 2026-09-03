"""MarketData-level checks that the volume guard actually fires on the frames
the scorer reads — closing the gap between the pure guard_volume unit tests and
the live fetch path. Network-free: `_yf_history` / `_fetch_history` are mocked.
"""
from __future__ import annotations
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.marketdata import MarketData

ET = ZoneInfo("America/New_York")


def _md():
    md = MarketData.__new__(MarketData)          # skip Alpaca client construction
    md._today_cache = {}
    md._hist_cache = {}
    md._leader_cache = {}
    return md


def _frame(rows):
    idx = pd.DatetimeIndex([r[0] for r in rows])
    return pd.DataFrame({
        "Open": [r[1] for r in rows], "High": [r[2] for r in rows],
        "Low": [r[3] for r in rows], "Close": [r[4] for r in rows],
        "Volume": [float(r[5]) for r in rows],
    }, index=idx)


def test_fetch_today_guards_volume_and_keeps_raw(monkeypatch):
    md = _md()
    today = datetime.now(ET).date()
    base = datetime(today.year, today.month, today.day, 9, 30, tzinfo=ET)
    vols = [500, 400, 380, 360, 350, 19_000_000]
    rows = [(base + timedelta(minutes=5 * i), 100, 100.1, 99.9, 100, vols[i])
            for i in range(6)]
    monkeypatch.setattr(md, "_yf_history", lambda sym, period: _frame(rows))
    out = md.intraday_bars("SPY")
    # corrupt bar 5 capped to the running median of [500,400,380,360,350] = 380
    assert out["Volume"].iloc[5] == 380.0
    assert out["volume_raw"].iloc[5] == 19_000_000
    assert len(out) == 6


def test_build_rvol_baseline_guards_each_session_before_cumsum(monkeypatch):
    md = _md()
    today = datetime.now(ET).date()
    rows = []
    # 8 prior sessions (RVOL_MIN_SESSIONS), 6 bars each; corrupt ONE session's
    # last bar. A per-session guard caps it; a guard that spanned days — or none —
    # would let ~19M leak into the averaged cum.
    for d in range(8, 0, -1):
        day = today - timedelta(days=d)
        base = datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET)
        vols = [500, 400, 380, 360, 350, 340]
        if d == 1:
            vols = [500, 400, 380, 360, 350, 19_000_000]
        for i, v in enumerate(vols):
            rows.append((base + timedelta(minutes=5 * i), 100, 100.1, 99.9, 100, v))
    monkeypatch.setattr(md, "_fetch_history", lambda sym, days=30: _frame(rows))
    baseline = md.build_rvol_baseline("SPY", day=today)
    assert not baseline.empty
    # guarded: max cum ~2.3k. Unguarded it would be ~(7*2330 + 19M)/8 ≈ 2.4M.
    assert float(baseline["cum"].max()) < 10_000
