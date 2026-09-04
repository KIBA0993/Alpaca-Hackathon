"""test_volume_guard.py — the winsoriser that defends VWAP and relative_volume
against Yahoo's corrupt 5-minute volume bars.

Yahoo
intermittently serves a bar whose Volume field is 5-257x the real value while
the price fields on the same bar stay exact; relative_volume sums a CUMULATIVE
series, so one bad bar poisons every later scan of the session. These tests pin
the guard's shape, not a tuned constant.

Pure functions over DataFrames — no network, no filesystem.
"""
from __future__ import annotations
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import score

ET = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _pinned_guard_cfg(monkeypatch):
    """Pin the guard constants so a future edit to the defaults cannot move a test."""
    monkeypatch.setattr(score, "VOLUME_GUARD_MULTIPLE", 10.0)
    monkeypatch.setattr(score, "VOLUME_GUARD_MIN_BARS", 3)
    monkeypatch.setattr(score, "VOLUME_SANITY_DAY_MULTIPLE", 3.0)


def _series(vals):
    idx = pd.date_range("2026-08-27 09:30", periods=len(vals), freq="5min", tz=ET)
    return pd.Series([float(v) for v in vals], index=idx, name="Volume")


def test_clean_series_is_untouched():
    s = _series([500, 400, 380, 360, 350, 340, 330, 320])
    out, capped = score.guard_volume(s)
    assert capped == 0
    pd.testing.assert_series_equal(out, s)


def test_single_corrupt_bar_is_capped_to_the_running_median():
    s = _series([500, 400, 380, 360, 350, 19_000_000, 330, 320])
    out, capped = score.guard_volume(s)
    assert capped == 1
    # bars 0..4 guarded so far are [500, 400, 380, 360, 350] -> median 380
    assert out.iloc[5] == 380.0
    assert list(out.iloc[[0, 1, 2, 3, 4, 6, 7]]) == [500, 400, 380, 360, 350, 330, 320]


def test_a_corrupt_bar_cannot_raise_the_bar_for_later_bars():
    """The median must be taken over ALREADY-GUARDED values — else a corrupt bar
    left in the window lifts the median enough to wave the next one through."""
    s = _series([500, 400, 380, 360, 350, 19_000_000, 18_000_000, 320])
    out, capped = score.guard_volume(s)
    assert capped == 2
    assert out.iloc[5] == 380.0
    assert out.iloc[6] < 1_000


def test_guard_is_causal():
    """Guarding a prefix must equal the prefix of guarding the whole frame —
    the same function runs on today's partial session and on completed ones."""
    vals = [500, 400, 380, 360, 350, 19_000_000, 330, 320, 900, 310]
    whole, _ = score.guard_volume(_series(vals))
    for n in range(1, len(vals) + 1):
        prefix, _ = score.guard_volume(_series(vals[:n]))
        assert list(prefix) == list(whole)[:n], f"diverged at length {n}"


def test_opening_bars_are_never_capped():
    """min_bars=3: there is no honest median to judge the first bars against."""
    s = _series([9_000_000, 8_000_000, 7_000_000, 400, 380])
    out, capped = score.guard_volume(s)
    assert capped == 0
    assert list(out) == [9_000_000, 8_000_000, 7_000_000, 400, 380]


def test_legitimate_volume_spike_survives():
    """A ~6x burst on a real news print must not be clipped at a 10x cap."""
    s = _series([500, 400, 380, 360, 350, 2_280, 900, 600])
    out, capped = score.guard_volume(s)
    assert capped == 0


def test_multiple_of_zero_disables_the_guard(monkeypatch):
    monkeypatch.setattr(score, "VOLUME_GUARD_MULTIPLE", 0.0)
    s = _series([500, 400, 380, 360, 350, 19_000_000])
    out, capped = score.guard_volume(s)
    assert capped == 0
    assert out.iloc[5] == 19_000_000


def test_empty_and_none_are_safe():
    out, capped = score.guard_volume(pd.Series([], dtype=float))
    assert capped == 0
    out, capped = score.guard_volume(None)
    assert (out, capped) == (None, 0)


def test_guard_is_idempotent():
    """The guard runs in MarketData._fetch_today AND again inside relative_volume,
    so applying it twice must be a no-op."""
    s = _series([500, 400, 380, 360, 350, 19_000_000, 18_000_000, 320, 900])
    once, n1 = score.guard_volume(s)
    twice, n2 = score.guard_volume(once)
    assert n2 == 0
    assert list(twice) == list(once)


def _baseline(full_day=1_000_000.0, slots=8):
    """A baseline frame shaped like MarketData.build_rvol_baseline's output."""
    idx = pd.date_range("2026-08-27 09:30", periods=slots, freq="5min", tz=ET)
    cum = [full_day * (i + 1) / slots for i in range(slots)]
    return pd.DataFrame({"cum": cum}, index=idx)


def test_relative_volume_uses_the_guarded_total():
    base = _baseline()
    bars = pd.DataFrame(
        {"Volume": [500, 400, 380, 360, 350, 19_000_000]},
        index=pd.date_range("2026-08-27 09:30", periods=6, freq="5min", tz=ET))
    rel, expected = score.relative_volume(bars, base)
    # guarded total is 500+400+380+360+350+380 = 2,370, not 19,002,370
    assert rel == round(2_370 / expected, 2)


def test_relative_volume_abstains_on_an_unsalvageable_frame():
    """Past 3x a whole normal session the frame is not worth capping. None is
    the documented 'no opinion' answer and callers already handle it."""
    base = _baseline(full_day=1_000.0)
    bars = pd.DataFrame(
        {"Volume": [900, 900, 900, 900, 900, 900]},
        index=pd.date_range("2026-08-27 09:30", periods=6, freq="5min", tz=ET))
    assert score.relative_volume(bars, base) == (None, None)
