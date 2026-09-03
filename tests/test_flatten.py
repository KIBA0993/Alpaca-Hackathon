"""EOD flatten (agent._flatten): it must retry the force-close until nothing is
open, and if anything survives all attempts, log a loud 'eod_flatten_incomplete'
for a human. Built without network: we bypass Agent.__init__ and stub only the
pieces _flatten touches (scan_once, execu.open_positions, journal, sleep)."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import agent as agent_mod
from src.agent import Agent


def _pos(contract):
    return SimpleNamespace(contract=contract)


class _Execu:
    def __init__(self, positions):
        self._open = [_pos(c) for c in positions]

    @property
    def open_positions(self):
        return list(self._open)


class _Journal:
    def __init__(self):
        self.records = []

    def write(self, rec):
        self.records.append(rec)

    def console(self, rec):
        pass


def _agent(execu, scan_effect):
    a = object.__new__(Agent)                    # skip __init__ (no keys/network)
    a.cfg = SimpleNamespace(scan={"eod_flatten_attempts": 4,
                                  "eod_flatten_retry_seconds": 5})
    a.execu = execu
    a.journal = _Journal()
    a.scan_once = scan_effect                    # instance override; _flatten calls self.scan_once()
    return a


def test_flatten_stops_once_all_closed(monkeypatch):
    monkeypatch.setattr(agent_mod._time, "sleep", lambda *_: None)
    execu = _Execu(["SPY_c", "QQQ_c"])
    calls = {"n": 0}

    def scan():                                  # each pass closes one position
        calls["n"] += 1
        if execu._open:
            execu._open.pop()

    a = _agent(execu, scan)
    a._flatten()
    assert execu.open_positions == []
    assert calls["n"] == 2                        # two passes closed both; no wasted attempts
    assert not any(r["type"] == "eod_flatten_incomplete" for r in a.journal.records)


def test_flatten_logs_loudly_when_positions_survive(monkeypatch):
    monkeypatch.setattr(agent_mod._time, "sleep", lambda *_: None)
    execu = _Execu(["SPY_c"])                    # scan never closes it
    calls = {"n": 0}

    def scan():
        calls["n"] += 1                          # closes nothing (simulates persistent failure)

    a = _agent(execu, scan)
    a._flatten()
    assert calls["n"] == 4                        # exhausted the bounded attempts
    warns = [r for r in a.journal.records if r["type"] == "eod_flatten_incomplete"]
    assert len(warns) == 1 and warns[0]["open"] == ["SPY_c"]   # contract strings
