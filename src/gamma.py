"""Dealer-gamma regime read: gamma picks the entry MODE.

Where price-derived regime reads are all descendants of the same price series,
dealer gamma is a structurally different, mechanical input — it comes from
option positioning rather than from the tape (Barbon & Buraschi, *Gamma
Fragility*). The rule:

  net 0DTE dealer gamma > 0  ->  dealers are long gamma, they SELL rallies / BUY
                                 dips -> pinning / mean-reversion  ->  CHOP  ->  FADE
  net 0DTE dealer gamma < 0  ->  dealers are short gamma, they CHASE  ->  TREND ->  CHASE

SCOPE: this module is available but NOT enabled in the shipped configuration —
`gamma.enabled` is absent from config.json, so the agent runs the leader-regime
path. It is kept here because Cboe's feed is snapshot-only, so the only way to
evaluate the read is to record it forward against live sessions. The dealer SIGN
convention (short calls / long puts) is an explicit assumption, exposed as
`invert_sign` so it can be flipped without touching the maths.

Gamma is re-derived from Black-Scholes, NOT read from Cboe's own quantized `gamma`
field (it rounds ~30% of OI to zero). Only the 0DTE horizon is used — that is the
gamma that governs a 0DTE session. Pure `compute_regime(chain_data, today)` so it
unit-tests with no network; `fetch_chain` is the only side-effecting piece.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import urllib.request
from typing import Callable, Optional

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"


def fetch_chain(sym: str, timeout: int = 60) -> dict:
    """The one side-effecting call: Cboe's free delayed option chain (no key)."""
    req = urllib.request.Request(CBOE_URL.format(sym=sym),
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["data"]


def _parse_occ(option_symbol: str) -> tuple[dt.date, str, float]:
    b = option_symbol[-15:]
    return (dt.date(2000 + int(b[0:2]), int(b[2:4]), int(b[4:6])),
            b[6], int(b[7:]) / 1000.0)


def _bs_gamma(S: float, K: float, T: float, iv: float, r: float = 0.04) -> float:
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (S * iv * math.sqrt(T))


def compute_regime(chain_data: dict, today: dt.date,
                   invert_sign: bool = False) -> dict:
    """0DTE dealer-gamma metrics + mode, from one chain snapshot. Pure.

    Returns {'usable', 'mode', 'net_gex', 'flip', 'spot', 'call_wall',
    'put_wall', 'n_contracts'}. `usable` is False (mode None) whenever the chain
    is empty/missing or carries no same-day contracts with OI and IV — the caller
    must abstain, never guess.
    """
    unusable = {"usable": False, "mode": None, "net_gex": None, "flip": None,
                "spot": None, "call_wall": None, "put_wall": None, "n_contracts": 0}
    if not chain_data:
        return unusable
    spot = chain_data.get("close") or chain_data.get("current_price")
    if not spot or spot <= 0:
        return unusable
    sign_flip = -1.0 if invert_sign else 1.0
    sub = []
    for o in chain_data.get("options", []):
        try:
            exp, cp, k = _parse_occ(o["option"])
        except (KeyError, ValueError, IndexError):
            continue
        oi = o.get("open_interest", 0) or 0
        iv = o.get("iv", 0) or 0
        if oi > 0 and iv > 0 and exp == today:      # 0DTE only: expires today
            sub.append((cp, k, oi, iv))
    if not sub:
        return unusable
    gam: dict[float, float] = {}
    net = 0.0
    T = 0.5 / 365.0                                 # 0DTE: a fraction of a day left
    for cp, k, oi, iv in sub:
        g = _bs_gamma(spot, k, T, iv) * oi * 100 * spot * spot * 0.01
        s = sign_flip * (1 if cp == "C" else -1)
        gam[k] = gam.get(k, 0.0) + s * g
        net += s * g
    near = {k: v for k, v in gam.items() if abs(k / spot - 1) < 0.05}
    call_wall = max(near, key=near.get) if near else None
    put_wall = min(near, key=near.get) if near else None
    # zero-gamma flip: scan the strike-gamma profile for a sign change
    flip = None
    if near:
        ks = sorted(near)
        prof = [near[k] for k in ks]
        for i in range(1, len(prof)):
            if prof[i] == 0 or (prof[i] > 0) != (prof[i - 1] > 0):
                denom = (prof[i] - prof[i - 1]) or 1.0
                flip = ks[i - 1] + (ks[i] - ks[i - 1]) * (-prof[i - 1]) / denom
                break
    mode = "chop" if net > 0 else "trend"           # long gamma -> fade; short -> chase
    return {"usable": True, "mode": mode, "net_gex": round(net, 0),
            "flip": round(flip, 2) if flip else None, "spot": round(float(spot), 2),
            "call_wall": call_wall, "put_wall": put_wall, "n_contracts": len(sub)}


def gamma_regime(symbols, today: Optional[dt.date] = None,
                 fetcher: Callable[[str], dict] = fetch_chain,
                 invert_sign: bool = False) -> dict:
    """Per-symbol 0DTE gamma regime. Network failures fail SAFE (usable=False).

    `fetcher` is injectable so tests pass a canned chain and touch no network.
    """
    today = today or dt.datetime.now(dt.timezone.utc).astimezone().date()
    out = {}
    for sym in symbols:
        try:
            out[sym] = compute_regime(fetcher(sym), today, invert_sign=invert_sign)
        except Exception as exc:                    # any fetch/parse failure -> abstain
            out[sym] = {"usable": False, "mode": None, "net_gex": None,
                        "flip": None, "spot": None, "call_wall": None,
                        "put_wall": None, "n_contracts": 0, "error": repr(exc)}
    return out
