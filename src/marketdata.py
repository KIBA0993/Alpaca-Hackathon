"""Market data: intraday 5m bars (yfinance), rvol/band baselines, options (Alpaca).

Underlying intraday 5m bars come from **yfinance** — the same real-time source
the live arms use — because Alpaca's free plan delays the SIP feed by 15
minutes, which starves a 0DTE scanner of the current session (at 09:49 the SIP
feed still only had the 09:30 bar). yfinance's known corrupt-volume bars are
winsorised by score.guard_volume, ported behaviourally from the live arms.

Option contracts, option quotes, and the leader DAILY bars (regime) stay on
Alpaca: completed daily bars are not delayed, and execution is Alpaca-only.
Baseline math mirrors _rvol_baseline_live / _band_baseline_live from
intraday_0dte.py, now fed by yfinance 5m bars with the identical volume guard.
"""
from __future__ import annotations
import time as _clock   # NB: `time` (below) is datetime.time; keep the clock separate
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

import pandas as pd
import yfinance as yf

from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, OptionLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import ContractType, AssetStatus

from .score import guard_volume

ET = ZoneInfo("America/New_York")

RVOL_LOOKBACK_DAYS = 10
RVOL_MIN_SESSIONS = 8
BAND_LOOKBACK_DAYS = 14
BAND_MIN_SESSIONS = 10
HIST_DAYS = 20          # calendar days of 5m history behind BOTH baselines (matches the live arms)
LEADER_FETCH_DAYS = 90      # ~60 sessions: comfortably more than the 20 an SMA needs
RTH_OPEN_MOD = 570      # 09:30
RTH_CLOSE_MOD = 960     # 16:00
YF_RETRIES = 3          # this NAS IP also serves the 5 live arms polling yfinance
YF_RETRY_SLEEP = 0.8    # seconds between attempts on a transient failure/empty


class MarketData:
    def __init__(self, key: str, secret: str, paper: bool = True):
        self._key, self._secret = key, secret
        self.stock = StockHistoricalDataClient(key, secret)
        self.option = OptionHistoricalDataClient(key, secret)
        self.trading = TradingClient(key, secret, paper=paper)
        self._today_cache: dict[str, pd.DataFrame] = {}
        self._hist_cache: dict[tuple, pd.DataFrame] = {}
        self._leader_cache: dict[tuple, dict[str, list[float]]] = {}

    # ------------------------------------------------------------------ bars
    def _yf_history(self, symbol: str, period: str) -> pd.DataFrame:
        """Raw 5-minute yfinance bars for `period`, regular session only, ET index.

        Retries a few times on a transient failure or empty response: this
        container shares its NAS IP with the five live arms, all polling yfinance
        every 5 minutes, so an occasional rate-limit is expected. On total
        failure it returns an empty frame — the scorer then simply skips the
        symbol for that scan (no crash, no stale carry-over).
        """
        df = None
        for attempt in range(YF_RETRIES):
            try:
                df = yf.Ticker(symbol).history(period=period, interval="5m",
                                               auto_adjust=True)
            except Exception:
                df = None
            if df is not None and not df.empty:
                break
            if attempt < YF_RETRIES - 1:
                _clock.sleep(YF_RETRY_SLEEP)
        if df is None or df.empty:
            return pd.DataFrame()
        idx = df.index
        # yfinance intraday is usually tz-aware ET already; localise if not.
        idx = idx.tz_localize("UTC").tz_convert(ET) if idx.tz is None else idx.tz_convert(ET)
        out = pd.DataFrame({
            "Open": df["Open"].to_numpy(float),
            "High": df["High"].to_numpy(float),
            "Low": df["Low"].to_numpy(float),
            "Close": df["Close"].to_numpy(float),
            "Volume": df["Volume"].to_numpy(float),
        }, index=idx)
        m = out.index.hour * 60 + out.index.minute
        return out[(m >= RTH_OPEN_MOD) & (m <= RTH_CLOSE_MOD)]

    def _fetch_today(self, symbol: str, day: Optional[date] = None) -> pd.DataFrame:
        """Today's regular-session 5m bars, ascending, ET index, VOLUME-GUARDED.

        A light 2-day pull (not the 30-day history): this is refetched every scan
        to grow the session bar by bar, so it must stay cheap. The corrupt-volume
        guard runs here — on the frame the scorer actually reads — protecting both
        relative_volume and VWAP. The raw column is kept for auditing; the frame
        is copied, never mutated in place.
        """
        day = day or datetime.now(ET).date()
        if symbol in self._today_cache:
            return self._today_cache[symbol]
        allb = self._yf_history(symbol, period="2d")
        today = allb[allb.index.date == day] if not allb.empty else allb
        if not today.empty:
            guarded, capped = guard_volume(today["Volume"])
            if capped:
                today = today.copy()
                today["volume_raw"] = today["Volume"].to_numpy()
                today["Volume"] = guarded.to_numpy()
        self._today_cache[symbol] = today
        return today

    def _fetch_history(self, symbol: str, days: int = HIST_DAYS) -> pd.DataFrame:
        """Trailing `days` calendar days of regular-session 5m bars for the baselines.

        Cached per run and NOT cleared between scans (baselines change once a
        day), so each symbol pulls its history exactly once — keeping yfinance
        load, and thus the rate-limit risk, down.
        """
        key = (symbol, days)
        if key in self._hist_cache:
            return self._hist_cache[key]
        out = self._yf_history(symbol, period=f"{days}d")
        self._hist_cache[key] = out
        return out

    def reset_intraday_cache(self) -> None:
        """Drop the per-scan today-bar cache so the next scan pulls fresh bars.

        Baselines (the heavy history) live in a separate cache that is NOT
        cleared — they change once a day and are memoized by the caller. Today's
        session, however, grows a bar every 5 minutes, so a long-running loop
        must clear this between scans or it would re-score the first snapshot.
        """
        self._today_cache.clear()

    def intraday_bars(self, symbol: str, day: Optional[date] = None) -> pd.DataFrame:
        """Today's regular-session 5m bars, ascending, ET index, volume-guarded."""
        return self._fetch_today(symbol, day)

    # -------------------------------------------------------------- baselines
    def build_rvol_baseline(self, symbol: str, day: Optional[date] = None
                            ) -> pd.DataFrame:
        """Avg cumulative volume at each 5m slot over the last N completed sessions."""
        day = day or datetime.now(ET).date()
        allb = self._fetch_history(symbol)
        if allb.empty:
            return pd.DataFrame()
        g = pd.DataFrame({"vol": allb["Volume"].to_numpy(float)}, index=allb.index)
        g["date"] = g.index.date
        g["mod"] = g.index.hour * 60 + g.index.minute
        g = g[g["date"] < day]
        if g.empty:
            return pd.DataFrame()
        # The 30-day history carries the same corruption as today's fetch, so the
        # denominator needs the identical per-session guard — a clean numerator
        # over a dirty denominator measures worse than guarding neither side.
        g = g.sort_index()
        g["vol"] = g.groupby("date")["vol"].transform(lambda s: guard_volume(s)[0])
        g["cum"] = g.groupby("date")["vol"].cumsum()
        recent = sorted(g["date"].unique())[-RVOL_LOOKBACK_DAYS:]
        if len(recent) < RVOL_MIN_SESSIONS:
            return pd.DataFrame()
        base = g[g["date"].isin(recent)].groupby("mod")["cum"].mean()
        idx = pd.DatetimeIndex(
            [datetime.combine(day, time(m // 60, m % 60), tzinfo=ET) for m in base.index])
        return pd.DataFrame({"cum": base.to_numpy()}, index=idx)

    def build_band_baseline(self, symbol: str, day: Optional[date] = None
                            ) -> pd.DataFrame:
        """Typical |close/open-1| at each 5m slot + prior close, last N sessions."""
        day = day or datetime.now(ET).date()
        allb = self._fetch_history(symbol)
        if allb.empty:
            return pd.DataFrame()
        # Price-only (no volume), so no guard is needed here — corruption is
        # confined to the Volume field.
        g = pd.DataFrame({"open": allb["Open"].to_numpy(float),
                          "close": allb["Close"].to_numpy(float)}, index=allb.index)
        g["date"] = g.index.date
        g["mod"] = g.index.hour * 60 + g.index.minute
        g = g[g["date"] < day]
        if g.empty:
            return pd.DataFrame()
        recent = sorted(g["date"].unique())[-BAND_LOOKBACK_DAYS:]
        if len(recent) < BAND_MIN_SESSIONS:
            return pd.DataFrame()
        g = g[g["date"].isin(recent)].copy()
        g["sess_open"] = g.groupby("date")["open"].transform("first")
        g = g[g["sess_open"] > 0]
        if g.empty:
            return pd.DataFrame()
        g["dev"] = (g["close"] / g["sess_open"] - 1).abs()
        sigma = g.groupby("mod")["dev"].mean()
        prev = g[g["date"] == recent[-1]]
        if prev.empty:
            return pd.DataFrame()
        prev_close = float(prev["close"].iloc[-1])
        idx = pd.DatetimeIndex(
            [datetime.combine(day, time(m // 60, m % 60), tzinfo=ET) for m in sigma.index])
        return pd.DataFrame({"sigma": sigma.to_numpy(), "prev_close": prev_close}, index=idx)

    # ----------------------------------------------------------- leader breadth
    def leader_closes(self, symbols: list[str], day: Optional[date] = None,
                      sma_days: int = 20) -> dict[str, list[float]]:
        """Daily closes per leader, ascending, ending at the LAST COMPLETED session.

        The no-lookahead guarantee lives here: any bar dated `day` or later is
        dropped, so an intraday call can never see today's partial daily bar. The
        caller (regime.leader_regime) then reads closes[-1] as "yesterday".

        Cached per (day, symbols) — daily bars do not change during a session.
        """
        day = day or datetime.now(ET).date()
        key = (day, tuple(sorted(symbols)))
        if key in self._leader_cache:
            return self._leader_cache[key]
        out: dict[str, list[float]] = {}
        try:
            req = StockBarsRequest(
                symbol_or_symbols=list(symbols),
                timeframe=TimeFrame(1, TimeFrameUnit.Day),
                # Enough calendar days to guarantee `sma_days` SESSIONS (~7/5 of
                # them) plus slack, so raising leader_sma_days cannot silently
                # starve the basket into abstaining.
                start=datetime.now(ET) - timedelta(
                    days=max(LEADER_FETCH_DAYS, int(sma_days) * 2 + 30)),
            )
            df = self.stock.get_stock_bars(req).df
        except Exception:
            df = None
        if df is None or getattr(df, "empty", True):
            self._leader_cache[key] = out
            return out
        df = df.reset_index()
        # Alpaca stamps a daily bar at the session date in UTC; convert before
        # comparing to an ET calendar date or the boundary is off by one.
        dts = pd.DatetimeIndex(df["timestamp"]).tz_convert(ET).date
        df = df.assign(_d=dts)
        df = df[df["_d"] < day]                    # <-- drops today's partial bar
        for sym, g in df.groupby("symbol"):
            g = g.sort_values("_d")
            out[str(sym)] = [float(c) for c in g["close"].to_numpy(float)]
        self._leader_cache[key] = out
        return out

    # ----------------------------------------------------------------- options
    def atm_contract(self, symbol: str, direction: str, day: Optional[date] = None,
                     spot: Optional[float] = None) -> Optional[dict]:
        """Nearest-expiry ATM option contract for `direction` ('call'/'put').

        Prefers today's expiration (0DTE); falls back to the soonest listed one.
        Returns {'symbol','strike','expiration'} or None.
        """
        day = day or datetime.now(ET).date()
        ctype = ContractType.CALL if direction == "call" else ContractType.PUT
        if spot is None:
            spot = self.latest_price(symbol)
        if not spot:
            return None
        req = GetOptionContractsRequest(
            underlying_symbols=[symbol],
            status=AssetStatus.ACTIVE,
            expiration_date_gte=day,
            type=ctype,
            strike_price_gte=str(round(spot * 0.97, 2)),
            strike_price_lte=str(round(spot * 1.03, 2)),
            limit=500,
        )
        contracts = self.trading.get_option_contracts(req).option_contracts or []
        if not contracts:
            return None
        soonest = min(c.expiration_date for c in contracts)
        near = [c for c in contracts if c.expiration_date == soonest]
        best = min(near, key=lambda c: abs(float(c.strike_price) - spot))
        return {"symbol": best.symbol, "strike": float(best.strike_price),
                "expiration": str(best.expiration_date)}

    def latest_price(self, symbol: str) -> Optional[float]:
        bars = self.intraday_bars(symbol)
        if not bars.empty:
            return float(bars["Close"].iloc[-1])
        allb = self._fetch_history(symbol)
        return float(allb["Close"].iloc[-1]) if not allb.empty else None

    def option_quote(self, occ_symbol: str) -> Optional[dict]:
        """Latest option quote -> {'bid','ask','mid'} or None."""
        try:
            req = OptionLatestQuoteRequest(symbol_or_symbols=occ_symbol)
            q = self.option.get_option_latest_quote(req).get(occ_symbol)
        except Exception:
            return None
        if q is None:
            return None
        bid, ask = float(q.bid_price or 0), float(q.ask_price or 0)
        if bid <= 0 and ask <= 0:
            return None
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (ask or bid)
        return {"bid": bid, "ask": ask, "mid": round(mid, 2)}
