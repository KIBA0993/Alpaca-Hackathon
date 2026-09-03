"""Faithfulness tests for the arm-E scorer — no network."""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.score import (_score_direction, compute_vwap, compute_rsi,
                       opening_range, noise_band, score_symbol, BAND_UNKNOWN)

ET = ZoneInfo("America/New_York")


def _bars(prices, vols=None, day="2026-08-27"):
    """Build a 5m bar frame from a close-price list starting 09:30 ET."""
    base = datetime.fromisoformat(day + "T09:30:00").replace(tzinfo=ET)
    idx = [base + timedelta(minutes=5 * i) for i in range(len(prices))]
    vols = vols or [1000] * len(prices)
    return pd.DataFrame({
        "Open": prices, "High": [p + 0.05 for p in prices],
        "Low": [p - 0.05 for p in prices], "Close": prices, "Volume": vols,
    }, index=pd.DatetimeIndex(idx))


def test_score_direction_weights_call():
    # every bullish atom fires except cp_ratio (None) => 0.22+0.22+0.18+0.15+0.13
    s, sig = _score_direction("call", price=101, vwap=100, or_high=100.5, or_low=99,
                              rsi=60, ema9=101, ema21=100, rel_vol=1.0,
                              cp_ratio=None, min_rel=0.92)
    assert s == pytest.approx(0.90, abs=1e-9)
    assert len(sig) == 5


def test_score_direction_overbought_penalty():
    s, _ = _score_direction("call", price=101, vwap=100, or_high=100.5, or_low=99,
                            rsi=80, ema9=101, ema21=100, rel_vol=1.0,
                            cp_ratio=None, min_rel=0.92)
    # above vwap .22 + above or .22 + (rsi>75 -> -.15) + ema .15 + rvol .13 = .57
    assert s == pytest.approx(0.57, abs=1e-9)


def test_score_direction_rel_vol_below_min_drops_atom():
    s, _ = _score_direction("call", price=101, vwap=100, or_high=100.5, or_low=99,
                            rsi=60, ema9=101, ema21=100, rel_vol=0.5,
                            cp_ratio=None, min_rel=0.92)
    assert s == pytest.approx(0.77, abs=1e-9)   # 0.90 - 0.13


def test_score_direction_cp_ratio_atom():
    s, _ = _score_direction("call", price=101, vwap=100, or_high=100.5, or_low=99,
                            rsi=60, ema9=101, ema21=100, rel_vol=1.0,
                            cp_ratio=1.5, min_rel=0.92)
    assert s == pytest.approx(1.0, abs=1e-9)    # 0.90 + 0.10 -> capped at 1.0


def test_vwap_and_or():
    b = _bars([100, 101, 102, 103])
    assert compute_vwap(b) > 0
    oh, ol = opening_range(b, or_minutes=15)     # first 3 bars (09:30,35,40)
    assert oh == pytest.approx(102.05, abs=1e-6)
    assert ol == pytest.approx(99.95, abs=1e-6)


def test_rsi_none_when_short():
    assert compute_rsi(pd.Series([1, 2, 3])) is None


def test_noise_band_unknown_without_baseline():
    b = _bars([100, 101, 102])
    assert noise_band(b, None, "half_or", 101, 100)["state"] is None


def test_noise_band_half_or_states():
    b = _bars([100, 100.5, 101], day="2026-08-27")
    # baseline: sigma column present, prev_close 100; slots at/under last bar time
    idx = pd.DatetimeIndex([datetime.fromisoformat("2026-08-27T09:30:00").replace(tzinfo=ET)
                            + timedelta(minutes=5 * i) for i in range(3)])
    base = pd.DataFrame({"sigma": [0.001, 0.001, 0.001], "prev_close": [100, 100, 100]}, index=idx)
    band = noise_band(b, base, "half_or", or_high=100.6, or_low=100.0)
    assert band["state"] in ("above", "below", "inside")
    # width from OR: (100.6-100.0)/101/2 ~ 0.00297; upper=max(open*(1+w),100)
    assert band["scale"] == "half_or"


def test_score_symbol_structure():
    b = _bars([100, 100.5, 101, 101.5, 102])
    out = score_symbol("SPY", b, {"min_score": 0.70, "or_minutes": 15,
                                  "min_relative_volume": 0.92, "noise_band_scale": "half_or"})
    assert set(out) >= {"symbol", "direction", "score", "noise_band", "key_signals"}
    assert out["direction"] in ("call", "put", "skip")
    assert out["noise_band"]["state"] is None   # no baseline supplied -> abstain


def test_score_symbol_insufficient_bars():
    out = score_symbol("SPY", _bars([100, 101]), {})
    assert out["direction"] == "skip"
