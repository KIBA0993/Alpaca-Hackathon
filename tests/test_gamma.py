"""The '#2' gamma-regime entry: regime computation, fade scoring, and the
agent's mode-routing + fail-safe abstain. No network — chains are canned."""
import sys
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.gamma import compute_regime, gamma_regime
from src.score import _score_fade, score_symbol
from src.agent import Agent
from src.gate import RulesGate

ET = ZoneInfo("America/New_York")
TODAY = dt.date(2026, 9, 1)


def _occ(sym, d, cp, strike):
    return f"{sym}{d:%y%m%d}{cp}{int(round(strike*1000)):08d}"


def _chain(spot, call_oi, put_oi, exp=TODAY, iv=0.20):
    """One-strike-ATM chain: relative call vs put OI sets the net-gamma sign."""
    return {"close": spot, "options": [
        {"option": _occ("SPY", exp, "C", spot), "open_interest": call_oi, "iv": iv},
        {"option": _occ("SPY", exp, "P", spot), "open_interest": put_oi, "iv": iv},
    ]}


# ---- compute_regime ---------------------------------------------------------
def test_positive_net_gamma_is_chop_fade():
    r = compute_regime(_chain(500, call_oi=10000, put_oi=100), TODAY)
    assert r["usable"] and r["mode"] == "chop" and r["net_gex"] > 0


def test_negative_net_gamma_is_trend_chase():
    r = compute_regime(_chain(500, call_oi=100, put_oi=10000), TODAY)
    assert r["usable"] and r["mode"] == "trend" and r["net_gex"] < 0


def test_invert_sign_flips_the_mode():
    base = compute_regime(_chain(500, 10000, 100), TODAY)
    inv = compute_regime(_chain(500, 10000, 100), TODAY, invert_sign=True)
    assert base["mode"] == "chop" and inv["mode"] == "trend"


def test_no_zero_dte_contracts_is_unusable():
    """A chain with only a LATER expiry must abstain, not guess off longer-dated OI."""
    later = compute_regime(_chain(500, 10000, 100, exp=dt.date(2026, 9, 30)), TODAY)
    assert later["usable"] is False and later["mode"] is None


def test_empty_or_missing_chain_is_unusable():
    for bad in ({}, {"close": 0, "options": []}, {"close": 500, "options": []}):
        assert compute_regime(bad, TODAY)["usable"] is False


def test_gamma_regime_fails_safe_on_fetch_error():
    def boom(sym):
        raise RuntimeError("cboe down")
    out = gamma_regime(["SPY", "QQQ"], today=TODAY, fetcher=boom)
    assert all(out[s]["usable"] is False and out[s]["mode"] is None for s in out)


def test_gamma_regime_injected_fetcher_no_network():
    out = gamma_regime(["SPY"], today=TODAY,
                       fetcher=lambda s: _chain(500, 10000, 100))
    assert out["SPY"]["mode"] == "chop"


# ---- fade scoring -----------------------------------------------------------
def test_fade_call_fires_at_or_low_below_vwap():
    # price at/under the OR low, stretched below VWAP, oversold -> strong bounce fade
    score, sig = _score_fade("call", price=99.0, vwap=101.0, or_high=105.0,
                             or_low=99.0, rsi=25.0, rel_vol=1.5, cp_ratio=0.5,
                             min_rel=0.92)
    assert score >= 0.70 and any("fade" in s for s in sig)


def test_fade_put_fires_at_or_high_above_vwap():
    score, _ = _score_fade("put", price=105.0, vwap=102.0, or_high=105.0,
                           or_low=99.0, rsi=80.0, rel_vol=1.5, cp_ratio=2.0,
                           min_rel=0.92)
    assert score >= 0.70


def test_fade_is_flat_in_the_middle_of_the_range():
    # mid-range, at VWAP, neutral RSI -> nothing to fade
    score, _ = _score_fade("call", price=102.0, vwap=102.0, or_high=105.0,
                           or_low=99.0, rsi=50.0, rel_vol=0.5, cp_ratio=1.0,
                           min_rel=0.92)
    assert score < 0.70


def test_score_symbol_chop_mode_uses_fade_labels():
    idx = pd.date_range("2026-09-01 09:30", periods=6, freq="5min", tz=ET)
    # a steadily falling tape: last price is the session low, below VWAP
    closes = [101.0, 100.5, 100.0, 99.5, 99.2, 99.0]
    bars = pd.DataFrame({"Open": closes, "High": [c + 0.1 for c in closes],
                         "Low": [c - 0.1 for c in closes], "Close": closes,
                         "Volume": [1e6] * 6}, index=idx)
    out = score_symbol("SPY", bars, {"min_score": 0.70}, mode="chop")
    assert out["entry_mode"] == "fade"


# ---- agent mode routing + fail-safe abstain ---------------------------------
class _StubMD:
    def reset_intraday_cache(self): pass
    def intraday_bars(self, symbol): return pd.DataFrame()   # empty -> skip
    def build_rvol_baseline(self, symbol): return pd.DataFrame()
    def build_band_baseline(self, symbol): return pd.DataFrame()


class _StubExec:
    trades_today = 0
    open_positions: list = []
    def manage(self, now, force_eod=False): return []
    def open_premium_usd(self): return 0.0


class _StubRisk:
    def should_flatten(self, now): return False


class _Cfg:
    symbols = ["SPY", "QQQ"]
    score = {"min_score": 0.70, "require_outside_noise_band": True}
    regime = {"require_leader_confirmation": False}
    gamma = {"enabled": True, "on_unavailable": "abstain"}
    entry_rules = {}
    scan = {}
    mode = "dry_run"


class _Journal:
    def __init__(self): self.records = []
    def write(self, rec): self.records.append(rec)
    def console(self, rec): pass


def _agent():
    a = object.__new__(Agent)
    a.md = _StubMD(); a.execu = _StubExec(); a.risk = _StubRisk(); a.cfg = _Cfg()
    a.gate = RulesGate(_Cfg.score, _Cfg.regime)
    a.journal = _Journal(); a._baselines = {}; a._regime_cache = {}
    a._gamma_cache = {}; a._gamma_on = True
    return a


def test_unusable_gamma_symbol_abstains_and_is_journaled():
    a = _agent()
    day = __import__("datetime").datetime.now(ET).date()
    a._gamma_cache[day] = {"SPY": {"usable": False, "mode": None, "error": "x"},
                           "QQQ": {"usable": True, "mode": "chop"}}
    a.scan_once()
    decs = [r for r in a.journal.records if r.get("type") == "decision"]
    spy = [r for r in decs if r["symbol"] == "SPY"][0]
    assert spy["gate"]["go"] is False and "unavailable" in spy["gate"]["rationale"]
    # QQQ was usable -> it did NOT hit the abstain path (reached scoring instead)
    qqq = [r for r in decs if r["symbol"] == "QQQ"][0]
    assert "unavailable" not in qqq["gate"]["rationale"]
