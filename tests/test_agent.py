"""Agent-loop robustness: a single failing symbol must not abort the scan,
and each scan must reset the intraday bar cache. No network."""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.agent import Agent
from src.gate import RulesGate

ET = ZoneInfo("America/New_York")


class _StubMD:
    def __init__(self):
        self.reset_calls = 0
        self.seen = []

    def reset_intraday_cache(self):
        self.reset_calls += 1

    def intraday_bars(self, symbol):
        self.seen.append(symbol)
        if symbol == "BAD":
            raise RuntimeError("boom")
        return pd.DataFrame()          # empty -> scores as "skip" -> no-go

    def build_rvol_baseline(self, symbol):
        return pd.DataFrame()

    def build_band_baseline(self, symbol):
        return pd.DataFrame()

    def leader_closes(self, symbols, day=None, sma_days=20):
        self.leader_calls = getattr(self, "leader_calls", 0) + 1
        return {s: [10.0] * 19 + [99.0] for s in symbols}      # all above -> bull


class _StubExec:
    trades_today = 0
    open_positions: list = []

    def manage(self, now, force_eod=False):
        return []

    def open_premium_usd(self):
        return 0.0

    def mins_since_last_entry(self, symbol, direction, now):
        return None

    def holds_open(self, symbol, direction):
        return False


class _StubRisk:
    def should_flatten(self, now):
        return False


class _StubCfg:
    symbols = ["GOOD1", "BAD", "GOOD2"]
    score = {"min_score": 0.70, "require_outside_noise_band": True}
    regime = {"require_leader_confirmation": True, "leader_min_above": 5}
    entry_rules = {}
    mode = "dry_run"


class _CaptureJournal:
    def __init__(self):
        self.records = []

    def write(self, rec):
        self.records.append(rec)

    def console(self, rec):
        pass


def _agent():
    a = object.__new__(Agent)          # skip __init__ (needs live keys)
    a.md = _StubMD()
    a.execu = _StubExec()
    a.risk = _StubRisk()
    a.cfg = _StubCfg()
    a.gate = RulesGate(_StubCfg.score)
    a.journal = _CaptureJournal()
    a._baselines = {}
    a._regime_cache = {}
    return a


def test_bad_symbol_does_not_abort_scan():
    a = _agent()
    a.scan_once()
    # BAD raised, but GOOD2 (after it) was still scanned
    assert a.md.seen == ["GOOD1", "BAD", "GOOD2"]
    # the failure was journaled as an error, not swallowed silently
    errs = [r for r in a.journal.records if r.get("type") == "error"]
    assert len(errs) == 1 and errs[0]["symbol"] == "BAD"


def test_scan_resets_intraday_cache():
    a = _agent()
    a.scan_once()
    assert a.md.reset_calls == 1


# ---------------------------------------------------------------------------
# Wiring added with the arm-C rebuild: regime is computed once, and the
# per-symbol entry-rule state actually reaches the RiskManager.
# ---------------------------------------------------------------------------
def test_regime_computed_once_per_session_not_per_symbol():
    """3 symbols x 2 scans must still be a single leader fetch for the day."""
    a = _agent()
    a.scan_once()
    a.scan_once()
    assert a.md.leader_calls == 1
    states = [r for r in a.journal.records if r.get("type") == "regime"]
    assert len(states) == 1 and states[0]["state"] == "bull"


def test_regime_state_is_journaled_on_every_decision():
    a = _agent()
    a.scan_once()
    decisions = [r for r in a.journal.records if r.get("type") == "decision"]
    assert decisions and all(d.get("regime") == "bull" for d in decisions)


def test_entry_rule_state_is_threaded_into_the_risk_check():
    """The cooldown can only work if the agent passes symbol-scoped state through."""
    from datetime import timedelta
    from src.risk import RiskManager
    seen = {}

    class _Risk(_StubRisk):
        def check_entry(self, st):
            seen["st"] = st
            return False, "stop here"

    class _Exec(_StubExec):
        def mins_since_last_exit(self, symbol, now):
            return 3.0

        def last_exit_reason(self, symbol):
            return "time_stop"

        def directions_for(self, symbol):
            return ("call",)

        def quote_entry(self, symbol, direction, spot=None, open_premium=0.0):
            return {"contract": {"symbol": "X", "strike": 1.0, "expiration": "x"},
                    "quote": {"bid": 1.0, "ask": 1.1, "mid": 1.05},
                    "cost_usd": 105.0, "qty": 1}

    a = _agent()
    a.risk = _Risk()
    a.execu = _Exec()
    a.gate = RulesGate({"min_score": 0.0, "require_outside_noise_band": False})

    class _MD(_StubMD):
        def intraday_bars(self, symbol):
            return pd.DataFrame()

    # drive one symbol through with a forced go
    a.cfg = type("C", (), dict(symbols=["SPY"], score={"min_score": 0.0,
                 "require_outside_noise_band": False}, regime={},
                 entry_rules={}, mode="dry_run"))()

    class _Gate:
        def decide(self, scored, regime=None):
            from src.gate import Decision
            return Decision(True, "call", "test", "forced")
    a.gate = _Gate()
    a.scan_once()

    st = seen.get("st")
    assert st is not None, "risk.check_entry was never reached"
    assert st.symbol == "SPY" and st.direction == "call"
    assert st.mins_since_last_exit == 3.0
    assert st.last_exit_reason == "time_stop"
    assert st.directions_today == ("call",)


def test_regime_defaults_to_full_basket_when_config_omits_min_symbols():
    """F1 guard: if leader_min_symbols is absent from config, the default must be
    the FULL configured basket — so a short basket ABSTAINS rather than silently
    becoming a stricter, bear-biased rule. Here 7 of 8 leaders have history; with
    the safe default (require 8) that is unusable -> state None. With the old
    default of 6 it would have produced a (bear-biasable) 5-of-7 verdict instead."""
    leaders = ["NVDA", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "TSLA", "AVGO"]

    class _MD(_StubMD):
        def leader_closes(self, symbols, day=None, sma_days=20):
            self.leader_calls = getattr(self, "leader_calls", 0) + 1
            out = {s: [10.0] * 19 + [99.0] for s in symbols}   # all above their SMA
            out[symbols[0]] = [10.0] * 5                        # one lacks history
            return out

    a = _agent()
    a.md = _MD()
    a.cfg = type("C", (), dict(
        symbols=["SPY"],
        score={"min_score": 0.70, "require_outside_noise_band": True},
        regime={"require_leader_confirmation": True, "leader_min_above": 5,
                "leader_symbols": leaders},          # NOTE: no leader_min_symbols
        entry_rules={}, mode="dry_run"))()

    reg = a._regime(datetime.now(ET))
    assert reg["counted"] == 7            # one leader had too little history
    assert reg["usable"] is False         # 7 < full basket of 8 -> not enough
    assert reg["state"] is None           # -> abstain, not a bear-biased verdict


def test_max_alerts_per_run_caps_entries_per_scan():
    """Arm C's per-scan entry cap (max_alerts_per_run): at most N NEW entries open
    in one scan pass across all symbols; the rest are skipped that pass and read as
    a max_alerts_per_run block, not stacked."""
    from src.execution import Position
    a = _agent()
    entered = []

    class _Risk(_StubRisk):
        def check_entry(self, st):
            return True, "ok"

    class _Exec(_StubExec):
        open_positions: list = []
        trades_today = 0

        def open_premium_usd(self):
            return 0.0

        def mins_since_last_exit(self, s, n):
            return None

        def last_exit_reason(self, s):
            return None

        def directions_for(self, s):
            return ()

        def mins_since_last_entry(self, s, d, n):
            return None

        def holds_open(self, s, d):
            return False

        def quote_entry(self, symbol, direction, spot=None, open_premium=0.0):
            return {"contract": {"symbol": symbol + "_P", "strike": 1.0,
                                 "expiration": "x"},
                    "quote": {"bid": 1.0, "ask": 1.1, "mid": 1.05},
                    "cost_usd": 105.0, "qty": 1}

        def enter(self, symbol, direction, prepared):
            entered.append(symbol)
            return Position(symbol=symbol, direction=direction,
                            contract=prepared["contract"]["symbol"], strike=1.0,
                            expiration="x", qty=1, entry_ask=1.1, entry_mid=1.05,
                            entry_time=datetime.now(ET), cost_usd=105.0)

    class _Gate:
        def decide(self, scored, regime=None):
            from src.gate import Decision
            return Decision(True, "put", "test", "forced")

    a.risk = _Risk()
    a.execu = _Exec()
    a.gate = _Gate()
    a.cfg = type("C", (), dict(
        symbols=["SPY", "QQQ", "IWM"],
        score={"min_score": 0.0, "require_outside_noise_band": False},
        regime={}, entry_rules={}, mode="dry_run",
        scan={"max_alerts_per_run": 2}))()

    a.scan_once()

    assert entered == ["SPY", "QQQ"]                 # third entry suppressed
    blocks = [r for r in a.journal.records if r.get("type") == "risk_block"
              and "max_alerts_per_run" in r.get("reason", "")]
    assert len(blocks) == 1 and blocks[0]["symbol"] == "IWM"


def test_manage_pass_runs_exits_without_an_entry_scan():
    """Arm B's fast loop calls _manage_pass on its own between entry scans: it must
    manage positions WITHOUT resetting the bar cache or scoring any symbol (that is
    the yfinance-touching half, kept on the slow cadence)."""
    a = _agent()
    a._manage_pass(datetime.now(ET))
    assert a.md.reset_calls == 0                       # entry pass (cache reset) not run
    assert not [r for r in a.journal.records if r.get("type") == "decision"]
    assert not a.md.seen                               # no symbol scored


def test_manage_exception_does_not_kill_the_scan():
    """An exception in manage() must not escape: the loop would die with 0DTE
    positions open and no EOD flatten — the worst possible failure."""
    class _BoomExec(_StubExec):
        def manage(self, now, force_eod=False):
            raise OSError("subprocess exploded")

    a = _agent()
    a.execu = _BoomExec()
    a.scan_once()                                     # must not raise
    errs = [r for r in a.journal.records
            if r.get("type") == "error" and r.get("where") == "manage"]
    assert len(errs) == 1 and "subprocess exploded" in errs[0]["reason"]
    # and the scan continued into the entry side
    assert [r for r in a.journal.records if r.get("type") == "decision"]
