"""The arm-E scoring core, reproduced faithfully from the live system.

This is a clean, self-contained copy of the logic in intraday_0dte.py
(score_symbol / _score_direction / noise_band / compute_vwap / compute_rsi /
opening_range). The weights and thresholds are IDENTICAL to the live arm-E
config. Nothing here is tuned to backtest P&L — a full year of testing showed
the entry signal carries no usable directional edge (docs/research.md), so the
core is kept exactly as it was measured, and the honest story is the product.

Every function is pure (DataFrame in, numbers out) so the scorer can be unit
tested against known values with no network. Bars are a pandas DataFrame indexed
by tz-aware ET timestamps with columns Open/High/Low/Close/Volume, ascending,
today's regular-session bars only. "Bars are STARTS": the last bar's start time
is the decision minute; its close is the price.
"""
from __future__ import annotations
from datetime import timedelta
from statistics import median as _median
from typing import Optional

import pandas as pd

OR_MINUTES = 15
DEFAULT_MIN_SCORE = 0.70

#: Corrupt-volume guard — behaviourally identical to the live arms'
#: intraday_0dte.guard_volume. Yahoo intermittently serves a 5-minute bar whose
#: Volume field is 5-257x the real value while its price fields stay exact.
#: relative_volume sums a CUMULATIVE series, so one bad bar poisons every later
#: scan of the session. Cap any bar above VOLUME_GUARD_MULTIPLE x the running
#: median of the (already-guarded) bars before it. Module-level so tests can pin
#: them; NOT a tuned parameter — anything in 10-12 measures the same (the arms
#: measured 96.6% agreement with Alpaca SIP guarded vs 70.9% unguarded).
VOLUME_GUARD_MULTIPLE = 10.0      # cap multiple over the running median (<=0 disables)
VOLUME_GUARD_MIN_BARS = 3         # opening bars have no honest median to judge against
VOLUME_SANITY_DAY_MULTIPLE = 3.0  # a session claiming > 3x a normal full day is unusable


def guard_volume(vol: Optional[pd.Series]) -> tuple[Optional[pd.Series], int]:
    """Winsorise corrupt volume bars. Returns (clean series, bars capped).

    Causal by construction: bar i is judged against the median of bars 0..i-1
    *as already guarded*, so guarding a partial session equals the prefix of
    guarding the whole one and no future bar can influence a past one. That
    matters because this same function guards today's partial frame AND each
    completed session behind the baseline. Only Volume is touched; the price
    fields on a corrupt bar are exact. Idempotent (running it twice is a no-op).
    """
    mult, min_bars = VOLUME_GUARD_MULTIPLE, VOLUME_GUARD_MIN_BARS
    if mult <= 0 or vol is None or len(vol) == 0:
        return vol, 0
    v = [float(x) for x in vol.to_numpy()]
    capped = 0
    for i in range(max(min_bars, 1), len(v)):
        med = _median(v[:i])
        if med > 0 and v[i] > mult * med:
            v[i] = med
            capped += 1
    if not capped:
        return vol, 0
    return pd.Series(v, index=vol.index, name=vol.name), capped

# noise_band() sentinel when it has no opinion. None is NOT "inside".
BAND_UNKNOWN = {"state": None, "sigma": None, "width": None, "scale": None,
                "upper": None, "lower": None}


def compute_vwap(bars: pd.DataFrame) -> float:
    if bars.empty:
        return 0.0
    tp = (bars["High"] + bars["Low"] + bars["Close"]) / 3
    vol = bars["Volume"].replace(0, pd.NA).fillna(1).astype(float)
    return float((tp * vol).sum() / vol.sum())


def compute_rsi(closes: pd.Series, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return round(float(val), 1) if pd.notna(val) else None


def opening_range(bars: pd.DataFrame, or_minutes: int = OR_MINUTES) -> tuple[float, float]:
    """High/low of the first `or_minutes` after 9:30 ET."""
    if bars.empty:
        return 0.0, 0.0
    open_dt = bars.index[0].replace(hour=9, minute=30, second=0, microsecond=0)
    if bars.index[0].date() != open_dt.date():
        open_dt = bars.index[0].normalize().replace(hour=9, minute=30)
    end_dt = open_dt + timedelta(minutes=or_minutes)
    or_bars = bars[(bars.index >= open_dt) & (bars.index < end_dt)]
    if or_bars.empty:
        or_bars = bars.head(max(1, or_minutes // 5))
    return float(or_bars["High"].max()), float(or_bars["Low"].min())


def relative_volume(bars: pd.DataFrame, rvol_baseline: Optional[pd.DataFrame]
                    ) -> tuple[Optional[float], Optional[float]]:
    """Today's cumulative volume vs this symbol's OWN same-time-of-day baseline.

    Returns (rel_vol, expected_now); both None when the baseline is missing.
    None means "no opinion" — never a granted bonus. `rvol_baseline` is a frame
    with a 'cum' column indexed by tz-aware ET slot timestamps (see marketdata).
    """
    if bars is None or bars.empty:
        return None, None
    if rvol_baseline is None or rvol_baseline.empty or "cum" not in rvol_baseline.columns:
        return None, None
    idx = rvol_baseline.index
    mods = idx.hour * 60 + idx.minute
    last = bars.index[-1]
    now_mod = last.hour * 60 + last.minute
    eligible = rvol_baseline[mods <= now_mod]
    if eligible.empty:
        return None, None
    expected = float(eligible["cum"].iloc[-1])
    if expected <= 0:
        return None, None
    # Guard the numerator the same way the baseline denominator is guarded (a
    # clean numerator over a dirty denominator measures worse than guarding
    # neither), then refuse to answer on a frame that is corrupt throughout: a
    # cap fixes one bad bar, not a whole bad session. None is the documented
    # "no opinion" — callers grant no volume bonus on it, which beats a
    # confident wrong one. (bars arriving from MarketData are already guarded,
    # so this cap is a no-op there; it defends direct callers too.)
    guarded, _ = guard_volume(bars["Volume"])
    total = float(guarded.sum())
    full_day = float(rvol_baseline["cum"].max())
    if (VOLUME_SANITY_DAY_MULTIPLE > 0 and full_day > 0
            and total > VOLUME_SANITY_DAY_MULTIPLE * full_day):
        return None, None
    return round(total / expected, 2), expected


def noise_band(bars: pd.DataFrame, band_baseline: Optional[pd.DataFrame],
               scale: str = "half_or",
               or_high: Optional[float] = None,
               or_low: Optional[float] = None) -> dict:
    """Where price sits against a band of this symbol's own noise (half_or).

    Faithful copy of intraday_0dte.noise_band. Returns a dict with 'state' in
    {'above','below','inside'} or None when the band cannot be drawn. None is
    NOT 'inside' — the band-gated arm must abstain, not decide, on None.

    The edges are widened to span the prior close, making the band deliberately
    asymmetric on a gap day (continuation over reversal). Do not "fix" that
    asymmetry — it is the band exactly as measured over the live history.
    """
    if bars is None or bars.empty:
        return dict(BAND_UNKNOWN)
    if (band_baseline is None or band_baseline.empty
            or "sigma" not in band_baseline.columns):
        return dict(BAND_UNKNOWN)
    idx = band_baseline.index
    mods = idx.hour * 60 + idx.minute
    last = bars.index[-1]
    eligible = band_baseline[mods <= last.hour * 60 + last.minute]
    if eligible.empty:
        return dict(BAND_UNKNOWN)
    try:
        sigma = float(eligible["sigma"].iloc[-1])
        prev_close = float(eligible["prev_close"].iloc[-1])
        session_open = float(bars["Open"].iloc[0])
        price = float(bars["Close"].iloc[-1])
    except (TypeError, ValueError, KeyError, IndexError):
        return dict(BAND_UNKNOWN)
    if not (sigma > 0 and session_open > 0 and prev_close > 0 and price > 0):
        return dict(BAND_UNKNOWN)
    width = sigma
    if scale == "half_or":
        if not (or_high and or_low and or_high > or_low):
            return dict(BAND_UNKNOWN)
        width = (float(or_high) - float(or_low)) / price / 2.0
        if width <= 0:
            return dict(BAND_UNKNOWN)
    elif scale != "sigma":
        return dict(BAND_UNKNOWN)
    upper = max(session_open * (1 + width), prev_close)
    lower = min(session_open * (1 - width), prev_close)
    state = "above" if price > upper else ("below" if price < lower else "inside")
    return {"state": state, "sigma": round(sigma, 5), "width": round(width, 5),
            "scale": scale, "upper": round(upper, 2), "lower": round(lower, 2)}


def _score_direction(direction: str, price: float, vwap: float,
                     or_high: float, or_low: float, rsi: Optional[float],
                     ema9: float, ema21: float, rel_vol: Optional[float],
                     cp_ratio: Optional[float], min_rel: float
                     ) -> tuple[float, list[str]]:
    score = 0.0
    signals: list[str] = []
    if direction == "call":
        if price > vwap:
            score += 0.22; signals.append(f"price ${price:.2f} above VWAP ${vwap:.2f}")
        if or_high > 0 and price > or_high:
            score += 0.22; signals.append(f"above opening range high ${or_high:.2f}")
        if rsi is not None and 48 <= rsi <= 68:
            score += 0.18; signals.append(f"RSI {rsi} in bullish zone")
        elif rsi is not None and rsi > 75:
            score -= 0.15; signals.append(f"RSI {rsi} overbought — fade risk")
        if ema9 > ema21:
            score += 0.15; signals.append("EMA9 > EMA21 bullish alignment")
        if rel_vol is not None and rel_vol >= min_rel:
            score += 0.13; signals.append(f"relative volume {rel_vol}x its own pace")
        if cp_ratio is not None and cp_ratio > 1.1:
            score += 0.10; signals.append(f"call/put vol ratio {cp_ratio}")
    else:
        if price < vwap:
            score += 0.22; signals.append(f"price ${price:.2f} below VWAP ${vwap:.2f}")
        if or_low > 0 and price < or_low:
            score += 0.22; signals.append(f"below opening range low ${or_low:.2f}")
        if rsi is not None and 32 <= rsi <= 52:
            score += 0.18; signals.append(f"RSI {rsi} in bearish zone")
        elif rsi is not None and rsi < 25:
            score -= 0.15; signals.append(f"RSI {rsi} oversold — bounce risk")
        if ema9 < ema21:
            score += 0.15; signals.append("EMA9 < EMA21 bearish alignment")
        if rel_vol is not None and rel_vol >= min_rel:
            score += 0.13; signals.append(f"relative volume {rel_vol}x its own pace")
        if cp_ratio is not None and cp_ratio < 0.9:
            score += 0.10; signals.append(f"call/put vol ratio {cp_ratio} (put-heavy)")
    return min(1.0, max(0.0, score)), signals


def _score_fade(direction: str, price: float, vwap: float,
                or_high: float, or_low: float, rsi: Optional[float],
                rel_vol: Optional[float], cp_ratio: Optional[float],
                min_rel: float) -> tuple[float, list[str]]:
    """FADE scoring for the gamma "chop/pin" mode — the MIRROR of _score_direction.

    Where the momentum core rewards a confirmed break (call above the OR high),
    the fade rewards a stretched EXTREME the market is expected to revert:
      fade-call = bounce off the OR LOW; fade-put = rejection off the OR HIGH.
    Same weights as the momentum atoms so `min_score` behaves identically; the
    EMA-alignment atom is dropped (trend alignment argues AGAINST a fade), so the
    fade tops out at 0.85 — still clears 0.70 on the two 0.22 anchors + one more.
    RSI and call/put are read contrarian (oversold -> buy the bounce). This path
    is UNVALIDATED (see src/gamma.py); it is kept deliberately simple, not tuned.
    """
    score = 0.0
    signals: list[str] = []
    if direction == "call":                          # fade a dip: expect a bounce UP
        if or_low > 0 and price <= or_low:
            score += 0.22; signals.append(f"at/below opening range low ${or_low:.2f} (fade)")
        if price < vwap:
            score += 0.22; signals.append(f"price ${price:.2f} stretched below VWAP ${vwap:.2f}")
        if rsi is not None and rsi < 32:
            score += 0.18; signals.append(f"RSI {rsi} oversold — bounce setup")
        if rel_vol is not None and rel_vol >= min_rel:
            score += 0.13; signals.append(f"relative volume {rel_vol}x its own pace")
        if cp_ratio is not None and cp_ratio < 0.9:
            score += 0.10; signals.append(f"call/put vol ratio {cp_ratio} (put-heavy capitulation)")
    else:                                            # fade a pop: expect a rejection DOWN
        if or_high > 0 and price >= or_high:
            score += 0.22; signals.append(f"at/above opening range high ${or_high:.2f} (fade)")
        if price > vwap:
            score += 0.22; signals.append(f"price ${price:.2f} stretched above VWAP ${vwap:.2f}")
        if rsi is not None and rsi > 68:
            score += 0.18; signals.append(f"RSI {rsi} overbought — rejection setup")
        if rel_vol is not None and rel_vol >= min_rel:
            score += 0.13; signals.append(f"relative volume {rel_vol}x its own pace")
        if cp_ratio is not None and cp_ratio > 1.1:
            score += 0.10; signals.append(f"call/put vol ratio {cp_ratio} (call-heavy euphoria)")
    return min(1.0, max(0.0, score)), signals


def score_symbol(symbol: str, bars: pd.DataFrame, cfg: dict,
                 rvol_baseline: Optional[pd.DataFrame] = None,
                 band_baseline: Optional[pd.DataFrame] = None,
                 cp_ratio: Optional[float] = None,
                 mode: str = "trend") -> dict:
    """Score bullish (call) and bearish (put) setups; return the stronger.

    `cfg` is the config 'score' block. `mode` selects the entry family:
      "trend" (default) -> momentum breakout (_score_direction), the shipped core
      "chop"            -> fade the OR edges (_score_fade), the gamma arm-B path
    Default "trend" keeps arm A and every existing caller byte-identical. The
    returned dict always carries the full reasoning (key_signals, noise_band, the
    raw indicators) so the agent's log is a complete, auditable record.
    """
    if bars is None or bars.empty or len(bars) < 3:
        return {"symbol": symbol, "direction": "skip", "score": 0.0,
                "skip_reason": "insufficient intraday bars",
                "key_signals": [], "noise_band": dict(BAND_UNKNOWN)}

    price = float(bars["Close"].iloc[-1])
    vwap = compute_vwap(bars)
    or_high, or_low = opening_range(bars, int(cfg.get("or_minutes", OR_MINUTES)))
    rsi = compute_rsi(bars["Close"])
    closes = bars["Close"]
    ema9 = float(closes.ewm(span=9, adjust=False).mean().iloc[-1])
    ema21 = float(closes.ewm(span=21, adjust=False).mean().iloc[-1])
    rel_vol, _ = relative_volume(bars, rvol_baseline)
    band = noise_band(bars, band_baseline, cfg.get("noise_band_scale", "half_or"),
                      or_high, or_low)
    min_rel = float(cfg.get("min_relative_volume", 1.0))

    if mode == "chop":
        call_score, call_sig = _score_fade("call", price, vwap, or_high, or_low,
                                           rsi, rel_vol, cp_ratio, min_rel)
        put_score, put_sig = _score_fade("put", price, vwap, or_high, or_low,
                                         rsi, rel_vol, cp_ratio, min_rel)
    else:
        call_score, call_sig = _score_direction("call", price, vwap, or_high, or_low,
                                                 rsi, ema9, ema21, rel_vol, cp_ratio, min_rel)
        put_score, put_sig = _score_direction("put", price, vwap, or_high, or_low,
                                              rsi, ema9, ema21, rel_vol, cp_ratio, min_rel)

    if call_score >= put_score:
        direction, score, signals = "call", call_score, call_sig
    else:
        direction, score, signals = "put", put_score, put_sig

    min_score = float(cfg.get("min_score", DEFAULT_MIN_SCORE))
    out = {
        "symbol": symbol,
        "direction": direction if score >= min_score else "skip",
        "would_have_direction": direction,
        "score": round(score, 3),
        "min_score": min_score,
        "entry_mode": "fade" if mode == "chop" else "momentum",
        "key_signals": signals,
        "rationale": "; ".join(signals[:4]),
        "patterns": {"rsi": rsi},
        "relative_volume": rel_vol,
        "noise_band": band,
        "underlying_price": round(price, 2),
        "vwap": round(vwap, 2),
        "or_high": round(or_high, 2),
        "or_low": round(or_low, 2),
    }
    if score < min_score:
        out["skip_reason"] = f"score {score:.2f} below min {min_score}"
    return out
