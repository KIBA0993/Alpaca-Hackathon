"""The agent loop: scan -> score -> gate -> risk -> execute -> manage.

Entry stack (arm C + half-OR band + leader regime):
    score >= min_score
    price OUTSIDE the half-OR noise band
    direction agrees with the leader-breadth regime ("T6")
    one direction per underlying per session
    15-minute cooldown on a symbol AFTER A `time_stop` EXIT (not blanket)

Run one pass (great for a demo):
    python -m src.agent --once
Run continuously through the session:
    python -m src.agent --loop
Flags override config.json:
    --mode dry_run|paper   --decision-mode rules_only|llm

Safe by default: mode=dry_run places no orders. Every scan is journaled with its
full reasoning to logs/decisions-YYYY-MM-DD.jsonl.
"""
from __future__ import annotations
import argparse
import time as _time
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import load_config
from .marketdata import MarketData
from .score import score_symbol
from .gate import make_gate
from .regime import leader_regime, DEFAULT_LEADERS
from .gamma import gamma_regime
from .llm import LLMAdvisor
from .risk import RiskManager, RiskState
from .execution import Executor
from .journal import Journal

ET = ZoneInfo("America/New_York")


class Agent:
    def __init__(self, cfg):
        self.cfg = cfg
        if not cfg.secrets.have_alpaca:
            raise SystemExit(
                "Missing ALPACA_API_KEY / ALPACA_SECRET_KEY. Copy .env.example to "
                ".env and fill them in (paper keys).")
        self.md = MarketData(cfg.secrets.alpaca_key, cfg.secrets.alpaca_secret,
                             paper=cfg.secrets.alpaca_paper)
        advisor = None
        if cfg.decision_mode == "llm":
            advisor = LLMAdvisor(cfg.secrets.anthropic_key,
                                 model=cfg.llm.get("model", "claude-sonnet-5"),
                                 max_tokens=int(cfg.llm.get("max_tokens", 1024)))
        self.gate = make_gate(cfg, advisor)
        self.risk = RiskManager(cfg.risk, cfg.entry_rules)
        day = datetime.now(ET).date().isoformat()
        self.journal = Journal(f"logs/decisions-{day}.jsonl")
        # Every CLI order command is journaled for a full audit trail.
        self.execu = Executor(self.md, cfg,
                              echo=lambda m: self.journal.write({"type": "cli", "cmd": m}))
        self._baselines: dict[str, tuple] = {}
        self._regime_cache: dict = {}
        self._gamma_cache: dict = {}
        # Arm B ("#2"): dealer-gamma regime picks the entry MODE per symbol
        # (chop -> fade the OR edges, trend -> chase breakouts) and T6 is off.
        # Any other arm leaves gamma.enabled false and runs the leader (T6) path.
        self._gamma_on = bool(cfg.gamma.get("enabled", False))
        print(f"# agent ready | mode={cfg.mode} decision={cfg.decision_mode} "
              f"symbols={cfg.symbols} paper={cfg.secrets.alpaca_paper}"
              f"{' | gamma-regime ON (arm B #2)' if self._gamma_on else ''}")

    def _regime(self, now: datetime) -> dict:
        """Leader-breadth regime for today, computed once and reused all session.

        Built from COMPLETED daily bars only (marketdata.leader_closes drops
        today's partial bar), so it is fixed for the whole session — the same
        value the backtest would have seen at the open.
        """
        day = now.date()
        if day in self._regime_cache:
            return self._regime_cache[day]
        rc = self.cfg.regime
        # dict.fromkeys dedups while preserving order: a duplicated leader name
        # would otherwise inflate len(syms) past what leader_closes can count
        # (it returns a symbol-keyed dict), silently pinning the basket to abstain.
        syms = list(dict.fromkeys(rc.get("leader_symbols") or DEFAULT_LEADERS))
        try:
            closes = self.md.leader_closes(syms, day=day,
                                           sma_days=int(rc.get("leader_sma_days", 20)))
            # Default min_symbols to the FULL configured basket, not a fixed 6:
            # the rule is calibrated as N-of-basket, so if leader_min_symbols were
            # ever dropped from config a smaller default would silently make it a
            # stricter, bear-biased rule. Requiring the whole basket fails safe
            # (a short basket -> unusable -> abstain) instead. (Review finding F1.)
            reg = leader_regime(closes,
                                min_above=int(rc.get("leader_min_above", 5)),
                                sma_days=int(rc.get("leader_sma_days", 20)),
                                min_symbols=int(rc.get("leader_min_symbols", len(syms))))
        except Exception as exc:
            reg = {"state": None, "above": 0, "counted": 0, "usable": False,
                   "detail": {}, "error": repr(exc)}
        self._regime_cache[day] = reg
        rec = {"type": "regime", "state": reg.get("state"),
               "above": reg.get("above"), "counted": reg.get("counted"),
               "detail": reg.get("detail"), "error": reg.get("error")}
        self.journal.write(rec); self.journal.console(rec)
        return reg

    def _gamma_regime(self, now: datetime) -> dict:
        """Per-symbol dealer-gamma regime for today (arm B "#2"), computed once
        and reused all session — the same session-open read the eventual
        validation will use. Returns {symbol: {mode, net_gex, flip, ...}}.

        Fetch failures fail SAFE: an unusable symbol carries mode=None, and
        _scan_symbol abstains on it (config gamma.on_unavailable), so a Cboe
        outage means "no trades", never a blind guess. Computed from the 0DTE
        chain expiring TODAY."""
        day = now.date()
        if day in self._gamma_cache:
            return self._gamma_cache[day]
        gcfg = self.cfg.gamma
        syms = list(dict.fromkeys(gcfg.get("symbols") or self.cfg.symbols))
        try:
            reg = gamma_regime(syms, today=day,
                               invert_sign=bool(gcfg.get("invert_sign", False)))
        except Exception as exc:                       # whole-fetch failure -> all abstain
            reg = {s: {"usable": False, "mode": None, "error": repr(exc)} for s in syms}
        self._gamma_cache[day] = reg
        for s, r in reg.items():
            rec = {"type": "gamma_regime", "symbol": s, "mode": r.get("mode"),
                   "net_gex": r.get("net_gex"), "flip": r.get("flip"),
                   "spot": r.get("spot"), "call_wall": r.get("call_wall"),
                   "put_wall": r.get("put_wall"), "usable": r.get("usable"),
                   "error": r.get("error")}
            self.journal.write(rec); self.journal.console(rec)
        return reg

    def _baseline(self, symbol: str):
        if symbol not in self._baselines:
            self._baselines[symbol] = (self.md.build_rvol_baseline(symbol),
                                       self.md.build_band_baseline(symbol))
        return self._baselines[symbol]

    def scan_once(self) -> None:
        """One full pass: manage exits, then (unless flattening) scan for entries.
        Used by --once and by the loop's entry cadence. The loop can also call
        `_manage_pass` on its own, faster cadence (arm B's 1-min exit management)."""
        now = datetime.now(ET)
        force_eod = self.risk.should_flatten(now.time())
        self._manage_pass(now, force_eod=force_eod)
        if force_eod:
            return
        self._entry_pass(now)

    def _manage_pass(self, now: datetime, force_eod: bool = False) -> None:
        """Manage exits on every open position. Reads only Alpaca option quotes
        (no yfinance), so it is safe to run on a fast cadence. An exception here
        would kill the loop with 0DTE positions still open and no EOD flatten —
        the worst failure — so it is caught and the positions retried next tick."""
        try:
            events = self.execu.manage(now, force_eod=force_eod)
        except Exception as exc:
            err = {"type": "error", "where": "manage", "reason": repr(exc)}
            self.journal.write(err); self.journal.console(err)
            events = []
        for ev in events:
            rec = {"type": "exit", "symbol": ev.position.symbol,
                   "contract": ev.position.contract, "reason": ev.reason,
                   "price": ev.exit_bid, "fill": ev.exit_fill,
                   "qty": ev.qty, "remaining": ev.position.qty,
                   "scaled": ev.position.scaled,
                   "pnl": ev.pnl_usd, "pnl_pct": ev.pnl_pct,
                   "mode": self.cfg.mode}
            self.journal.write(rec); self.journal.console(rec)

    def _entry_pass(self, now: datetime) -> None:
        """Score every symbol and take new entries. This is the yfinance-touching
        half (fresh intraday bars), so the loop runs it on the slower scan cadence."""
        # Intraday bars change every scan; drop the per-run bar cache so a --loop
        # pass re-fetches fresh bars instead of re-scoring the first snapshot.
        # (Baselines are memoized at the agent level, so they are not refetched.)
        self.md.reset_intraday_cache()
        # Per-scan entry cap (arm C's max_alerts_per_run): at most this many NEW
        # entries open in one scan pass across all symbols; 0 disables it. Read
        # defensively so a config without a `scan` section (unit-test stubs) is
        # simply uncapped rather than an AttributeError.
        scan_cfg = getattr(self.cfg, "scan", None) or {}
        self._max_alerts = int(scan_cfg.get("max_alerts_per_run", 0) or 0)
        self._entries_this_scan = 0
        regime = (self._gamma_regime(now)
                  if getattr(self, "_gamma_on", False) else self._regime(now))
        for symbol in self.cfg.symbols:
            try:
                self._scan_symbol(symbol, now, regime)
            except Exception as exc:  # one bad symbol must not abort the scan
                err = {"type": "error", "symbol": symbol, "reason": repr(exc)}
                self.journal.write(err); self.journal.console(err)
                continue

    def _scan_symbol(self, symbol: str, now: datetime,
                     regime: dict | None = None) -> None:
        # Arm B ("#2"): the per-symbol gamma regime picks the entry MODE. A
        # symbol whose gamma is unusable (Cboe outage, thin 0DTE chain) abstains
        # unless config says allow — in which case it falls back to momentum.
        score_mode = "trend"
        gsym = None
        gamma_on = getattr(self, "_gamma_on", False)
        if gamma_on:
            gsym = (regime or {}).get(symbol) or {}
            gmode = gsym.get("mode")
            if gmode is None:
                if str(self.cfg.gamma.get("on_unavailable", "abstain")) == "abstain":
                    r = {"type": "decision", "symbol": symbol, "gamma_mode": None,
                         "gate": {"go": False, "source": "gamma",
                                  "rationale": "gamma regime unavailable — abstain "
                                               f"({gsym.get('error', 'no 0DTE chain')})"}}
                    self.journal.write(r); self.journal.console(r)
                    return
                gmode = "trend"                        # on_unavailable=allow -> chase
            score_mode = "chop" if gmode == "chop" else "trend"
        bars = self.md.intraday_bars(symbol)
        rvol_base, band_base = self._baseline(symbol)
        scored = score_symbol(symbol, bars, self.cfg.score,
                              rvol_baseline=rvol_base, band_baseline=band_base,
                              cp_ratio=None, mode=score_mode)
        # Gamma arm passes no leader regime (T6 is off); the gate applies only the
        # score floor + noise band, and direction is whatever the mode's scorer chose.
        decision = self.gate.decide(scored, regime=None if gamma_on else regime)
        rec = {"type": "decision", "symbol": symbol, "score": scored.get("score"),
               "would_direction": scored.get("would_have_direction"),
               "entry_mode": scored.get("entry_mode"),
               "noise_band": (scored.get("noise_band") or {}).get("state"),
               "regime": (gsym.get("mode") if gamma_on
                          else (regime or {}).get("state")),
               "signals": scored.get("key_signals"),
               "gate": {"go": decision.go, "source": decision.source,
                        "rationale": decision.rationale, "detail": decision.detail}}
        self.journal.write(rec); self.journal.console(rec)
        if not decision.go:
            return

        # Per-scan entry cap (arm C's max_alerts_per_run). Checked here, after the
        # decision is journaled but before the option-chain call, so a suppressed
        # entry costs no quote and is still visible in the log.
        cap = getattr(self, "_max_alerts", 0)
        if cap > 0 and getattr(self, "_entries_this_scan", 0) >= cap:
            self.journal.write({"type": "risk_block", "symbol": symbol,
                                "reason": f"max_alerts_per_run reached ({cap}) "
                                          f"this scan"})
            return

        prepared = self.execu.quote_entry(
            symbol, decision.direction, spot=scored.get("underlying_price"),
            open_premium=self.execu.open_premium_usd())
        if not prepared:
            self.journal.write({"type": "skip", "symbol": symbol,
                                "reason": "no tradable ATM contract/quote, "
                                          "or no premium room for even one contract"})
            return
        st = RiskState(now=now.time(), open_positions=len(self.execu.open_positions),
                       trades_today=self.execu.trades_today,
                       premium_cost_usd=prepared["cost_usd"],
                       symbol=symbol, direction=decision.direction,
                       mins_since_last_exit=self.execu.mins_since_last_exit(symbol, now),
                       last_exit_reason=self.execu.last_exit_reason(symbol),
                       directions_today=self.execu.directions_for(symbol),
                       mins_since_last_entry=self.execu.mins_since_last_entry(
                           symbol, decision.direction, now),
                       already_holding=self.execu.holds_open(
                           symbol, decision.direction))
        ok, why = self.risk.check_entry(st)
        if not ok:
            r = {"type": "risk_block", "symbol": symbol, "reason": why,
                 "cost": prepared["cost_usd"]}
            self.journal.write(r); self.journal.console(r)
            return
        pos = self.execu.enter(symbol, decision.direction, prepared)
        self._entries_this_scan = getattr(self, "_entries_this_scan", 0) + 1
        rec = {"type": "entry", "symbol": symbol, "direction": decision.direction,
               "contract": pos.contract, "strike": pos.strike, "qty": pos.qty,
               "price": pos.entry_ask, "mid": pos.entry_mid, "fill": pos.entry_fill,
               "cost": pos.cost_usd, "gate_source": decision.source,
               "order_id": pos.order_id, "mode": self.cfg.mode}
        self.journal.write(rec); self.journal.console(rec)

    def loop(self) -> None:
        """Run through the session. Exits are managed every `manage_step_minutes`
        (default = scan cadence); entries are scanned every `scan_step_minutes`.
        With manage_step < scan_step (arm B: 1 vs 5) exits react each minute off
        Alpaca quotes while entry scoring stays on the slower yfinance cadence, so
        the fast loop adds no yfinance load. When they are equal (arm A) each tick
        does both, exactly as before."""
        scan_step = int(self.cfg.scan.get("scan_step_minutes", 5))
        manage_step = int(self.cfg.scan.get("manage_step_minutes", scan_step)
                          or scan_step)
        manage_step = max(1, min(manage_step, scan_step))
        last_entry_scan = None
        while True:
            now = datetime.now(ET)
            if self.risk.should_flatten(now.time()):
                self._flatten()
                print("# eod flatten reached — stopping")
                return
            self._manage_pass(now, force_eod=False)
            due = (last_entry_scan is None
                   or (now - last_entry_scan).total_seconds() >= scan_step * 60 - 1)
            if due:
                self._entry_pass(now)
                last_entry_scan = now
            _time.sleep(manage_step * 60)

    def _flatten(self) -> None:
        """Force-close every open position at EOD, and VERIFY it worked.

        A single best-effort pass is not enough: a momentary quote/CLI hiccup can
        leave a 0DTE open, and carrying it into expiration is exactly what we must
        never do. So we retry the flatten pass until nothing is open or we run out
        of attempts, then log loudly if anything remains for a human to close."""
        attempts = int(self.cfg.scan.get("eod_flatten_attempts", 4))
        retry_s = int(self.cfg.scan.get("eod_flatten_retry_seconds", 5))
        for i in range(attempts):
            self.scan_once()   # force_eod path closes everything it can
            if not self.execu.open_positions:
                break
            if i < attempts - 1:
                _time.sleep(retry_s)
        # Safety net: flatten any option position still held at the broker that
        # our in-memory book doesn't cover (orphans from a restart or a partial
        # fill), so nothing rides 0DTE into expiration.
        try:
            for s in self.execu.flatten_broker_stragglers():
                rec = {"type": "eod_broker_sweep", **s}
                self.journal.write(rec); self.journal.console(rec)
        except Exception as exc:
            self.journal.write({"type": "error", "where": "eod_broker_sweep",
                                "reason": repr(exc)})
        remaining = self.execu.open_positions
        if remaining:
            warn = {"type": "eod_flatten_incomplete",
                    "open": [p.contract for p in remaining],
                    "reason": f"still open after {attempts} flatten attempts — "
                              "CLOSE THESE MANUALLY before expiration"}
            self.journal.write(warn); self.journal.console(warn)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--mode", choices=["dry_run", "paper"])
    ap.add_argument("--decision-mode", choices=["rules_only", "llm"])
    ap.add_argument("--once", action="store_true", help="run a single scan pass")
    ap.add_argument("--loop", action="store_true", help="run through the session")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.mode:
        cfg.raw["mode"] = args.mode
    if args.decision_mode:
        cfg.raw["decision_mode"] = args.decision_mode

    agent = Agent(cfg)
    if args.loop:
        agent.loop()
    else:
        agent.scan_once()


if __name__ == "__main__":
    main()
