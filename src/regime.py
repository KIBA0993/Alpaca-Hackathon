"""Leader-breadth regime filter ("T6").

The rule, exactly as it was measured on the 264-session OPRA harness: count how
many of a fixed basket of mega-cap leaders closed above their OWN 20-day simple
moving average as of the LAST COMPLETED session. Five or more of eight => bullish
regime; four or fewer => bearish. Calls are "aligned" in a bullish regime, puts in
a bearish one; the filter only ever blocks the opposed side.

Two conventions matter and both mirror the research harness (`ctx.py`), so live and
backtest read the same number:

  * The SMA window INCLUDES the bar being compared: sma = mean(closes[-20:]) and
    the comparison price is closes[-1]. (This is the ordinary "close vs its own
    SMA-20" reading, and it is what split2.py/ctx.py both used.)
  * The last element must be the last COMPLETED session. Never pass today's
    partial bar — that is the lookahead trap. marketdata.leader_closes() drops it.

HONEST STATUS — read before trusting this: T6 is NOT an established edge. Over
264 sessions it measured +$16.59/position with a raw two-sided p of 0.068 and a
family-wise adjusted p of 0.405, and it is the largest of nine correlated tests,
so most of that point estimate is selection. The study's minimum detectable effect
was $23-34/position, i.e. it could not have resolved an effect this size either
way. Settling it needs ~1.6-3.4 years more data, not one quarter. It is wired in
here because it was asked for and because it is defensible as a DIRECTIONAL
CONSISTENCY rule, not because it is known to make money.
"""
from __future__ import annotations
from typing import Optional

DEFAULT_LEADERS = ["NVDA", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "TSLA", "AVGO"]


def leader_breadth(closes: dict[str, list[float]], sma_days: int = 20,
                   min_symbols: int = 6) -> dict:
    """How many leaders sit above their own SMA-`sma_days`.

    `closes[sym]` is an ASCENDING list of daily closes whose LAST element is the
    most recent COMPLETED session. Symbols with too little history are skipped.

    Returns {'above', 'counted', 'detail', 'usable'}. `usable` is False when
    fewer than `min_symbols` leaders had enough history — callers must abstain
    rather than guess, the same way the noise band does.
    """
    above = 0
    counted = 0
    detail: dict[str, Optional[bool]] = {}
    for sym in sorted(closes):
        series = [float(c) for c in (closes.get(sym) or []) if c is not None]
        if len(series) < sma_days:
            detail[sym] = None                     # not enough history to judge
            continue
        window = series[-sma_days:]
        sma = sum(window) / float(sma_days)
        is_above = series[-1] > sma
        detail[sym] = is_above
        counted += 1
        above += 1 if is_above else 0
    return {"above": above, "counted": counted, "detail": detail,
            "usable": counted >= int(min_symbols)}


def leader_regime(closes: dict[str, list[float]], min_above: int = 5,
                  sma_days: int = 20, min_symbols: int = 6) -> dict:
    """Binary bull/bear regime from leader breadth.

    Deliberately has NO neutral zone: the tested rule was a single split at
    `min_above`, and adding a middle band would be a different (untested) rule.
    Returns {'state': 'bull'|'bear'|None, 'above', 'counted', 'usable', 'detail'}.
    `state` is None when the basket is unusable — callers abstain.
    """
    b = leader_breadth(closes, sma_days=sma_days, min_symbols=min_symbols)
    state = None
    if b["usable"]:
        state = "bull" if b["above"] >= int(min_above) else "bear"
    return {"state": state, **b}


def aligned_direction(state: Optional[str]) -> Optional[str]:
    """The one direction this regime permits ('call' in a bull, 'put' in a bear)."""
    if state == "bull":
        return "call"
    if state == "bear":
        return "put"
    return None
