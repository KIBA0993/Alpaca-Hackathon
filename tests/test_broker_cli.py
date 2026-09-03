"""BrokerCLI robustness — the parts test_execution.py stubs out. No network: we
fake `subprocess.run` and a deterministic clock, so we can drive poll timeouts,
transient CLI blips, and the cancel-and-reconcile path exactly.

Covers the highest-risk gaps from review:
  * a poll that never sees terminal must CANCEL and reconcile, never orphan;
  * a transient CLI error mid-poll must NOT abort the poll;
  * the ALPACA_LIVE_TRADE seatbelt must refuse to place an order.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import broker_cli
from src.broker_cli import BrokerCLI, BrokerError


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _ok(payload) -> _Proc:
    return _Proc(0, json.dumps(payload))


def _err(msg="boom") -> _Proc:
    return _Proc(1, "", msg)


class _FakeCLI:
    """Dispatches `alpaca order {submit,get,cancel}` to _Proc responses.

    `get` returns items from `get` (a queue whose last item repeats, since poll
    calls get an unknown number of times) UNTIL a cancel happens; after cancel it
    returns `get_after_cancel` — that's the reconcile read submit_and_fill does."""
    def __init__(self, submit=None, get=None, cancel=None, get_after_cancel=None,
                 doctor=None, by_client=None):
        self.q = {"submit": list(submit or []), "cancel": list(cancel or [])}
        self.get = list(get or [])
        self.get_after_cancel = get_after_cancel
        self.doctor = doctor
        self.by_client = by_client
        self.canceled = False
        self.calls = []

    def _pop(self, queue):
        if not queue:
            raise AssertionError("no response queued")
        return queue[0] if len(queue) == 1 else queue.pop(0)

    def __call__(self, cmd, capture_output, text):
        self.calls.append(cmd)
        if cmd[1] == "doctor":           # ["alpaca", "doctor"]
            if self.doctor is None:
                raise AssertionError("no doctor response queued")
            return self.doctor
        sub = cmd[2]                      # ["alpaca", "order", "<sub>", ...]
        if sub == "cancel":
            self.canceled = True
            return self._pop(self.q["cancel"])
        if sub == "submit":
            return self._pop(self.q["submit"])
        if sub == "get-by-client-id":
            if self.by_client is None:
                raise AssertionError("no by_client response queued")
            return self.by_client
        if self.canceled and self.get_after_cancel is not None:
            return self.get_after_cancel
        return self._pop(self.get)

    def count(self, sub):
        return sum(1 for c in self.calls if len(c) > 2 and c[2] == sub)


@pytest.fixture
def clock(monkeypatch):
    """Deterministic clock: sleep advances time, so poll_timeout is exact."""
    now = {"t": 0.0}
    monkeypatch.setattr(broker_cli.time, "time", lambda: now["t"])
    monkeypatch.setattr(broker_cli.time, "sleep",
                        lambda s: now.__setitem__("t", now["t"] + s))
    return now


def _wire(monkeypatch, fake):
    monkeypatch.setattr(broker_cli.subprocess, "run",
                        lambda cmd, capture_output, text: fake(cmd, capture_output, text))


def test_poll_tolerates_transient_error_then_fills(clock, monkeypatch):
    # submit ok; first `get` blips (nonzero), second reports filled.
    fake = _FakeCLI(
        submit=[_ok({"id": "o1"})],
        get=[_err("temporary network glitch"),
             _ok({"status": "filled", "filled_avg_price": "1.23", "filled_qty": "1"})],
    )
    _wire(monkeypatch, fake)
    res = BrokerCLI(poll_timeout=45, poll_interval=2).submit_and_fill(
        "SPY260831C00640000", "buy", 1, "buy_to_open")
    assert res["status"] == "filled" and res["fill_price"] == 1.23
    assert fake.count("cancel") == 0            # never had to cancel


def test_timeout_cancels_then_reconciles_a_late_fill(clock, monkeypatch):
    # poll only ever sees "accepted" -> times out -> cancel -> final read shows it
    # actually filled. The fill must be captured, not orphaned.
    fake = _FakeCLI(
        submit=[_ok({"id": "o2"})],
        get=[_ok({"status": "accepted"})],           # every poll: not terminal
        cancel=[_ok({})],
        get_after_cancel=_ok({"status": "filled", "filled_avg_price": "0.90",
                              "filled_qty": "1"}),
    )
    _wire(monkeypatch, fake)
    res = BrokerCLI(poll_timeout=45, poll_interval=2).submit_and_fill(
        "SPY260831C00640000", "buy", 1, "buy_to_open")
    assert fake.count("cancel") == 1
    assert res["status"] == "filled" and res["fill_price"] == 0.90


def test_timeout_cancels_and_books_nothing_when_unfilled(clock, monkeypatch):
    # poll times out -> cancel -> final read shows canceled with no fill.
    fake = _FakeCLI(
        submit=[_ok({"id": "o3"})],
        get=[_ok({"status": "accepted"})],
        cancel=[_ok({})],
        get_after_cancel=_ok({"status": "canceled", "filled_avg_price": None,
                              "filled_qty": "0"}),
    )
    _wire(monkeypatch, fake)
    res = BrokerCLI(poll_timeout=45, poll_interval=2).submit_and_fill(
        "SPY260831C00640000", "buy", 1, "buy_to_open")
    assert fake.count("cancel") == 1
    assert res["status"] == "canceled" and res["fill_price"] is None


def test_timeout_unknown_final_state_raises(clock, monkeypatch):
    # poll times out, cancel attempted, but the final read ALSO errors -> we must
    # raise loudly (book nothing) rather than pretend anything.
    fake = _FakeCLI(
        submit=[_ok({"id": "o4"})],
        get=[_err("api down")],           # every get (poll + final) fails
        cancel=[_ok({})],
    )
    _wire(monkeypatch, fake)
    with pytest.raises(BrokerError, match="final state is unknown"):
        BrokerCLI(poll_timeout=10, poll_interval=2).submit_and_fill(
            "SPY260831C00640000", "buy", 1, "buy_to_open")


def test_done_for_day_is_terminal(clock, monkeypatch):
    fake = _FakeCLI(
        submit=[_ok({"id": "o5"})],
        get=[_ok({"status": "done_for_day", "filled_avg_price": None,
                  "filled_qty": "0"})],
    )
    _wire(monkeypatch, fake)
    res = BrokerCLI(poll_timeout=45, poll_interval=2).submit_and_fill(
        "SPY260831C00640000", "buy", 1, "buy_to_open")
    assert res["status"] == "done_for_day" and res["fill_price"] is None
    assert fake.count("cancel") == 0            # terminal, so no cancel needed


def test_live_trade_env_refuses_to_place(monkeypatch):
    fake = _FakeCLI(submit=[_ok({"id": "should-not-happen"})])
    _wire(monkeypatch, fake)
    monkeypatch.setenv("ALPACA_LIVE_TRADE", "1")
    with pytest.raises(BrokerError, match="paper-only"):
        BrokerCLI().submit_market("SPY260831C00640000", "buy", 1, "buy_to_open")
    assert fake.calls == []                     # nothing was ever sent to the CLI


def test_live_trade_env_refuses_on_exit_side_too(clock, monkeypatch):
    # The seatbelt must cover SELLs, not just BUYs (submit_and_fill -> submit_market).
    fake = _FakeCLI(submit=[_ok({"id": "nope"})])
    _wire(monkeypatch, fake)
    monkeypatch.setenv("ALPACA_LIVE_TRADE", "enabled")   # non-falsey, not in old allowlist
    with pytest.raises(BrokerError, match="paper-only"):
        BrokerCLI().submit_and_fill("SPY260831C00640000", "sell", 1, "sell_to_close")
    assert fake.calls == []


def test_timeout_nonterminal_reconcile_books_nothing(clock, monkeypatch):
    # timeout -> cancel -> final read is still non-terminal (pending_cancel). We must
    # NOT treat it as a fill: status is non-'filled' and fill_price is None.
    fake = _FakeCLI(
        submit=[_ok({"id": "o7"})],
        get=[_ok({"status": "accepted"})],
        cancel=[_ok({})],
        get_after_cancel=_ok({"status": "pending_cancel", "filled_avg_price": None,
                              "filled_qty": "0"}),
    )
    _wire(monkeypatch, fake)
    res = BrokerCLI(poll_timeout=45, poll_interval=2).submit_and_fill(
        "SPY260831C00640000", "buy", 1, "buy_to_open")
    assert res["status"] == "pending_cancel" and res["fill_price"] is None


def test_paper_env_unset_places_normally(clock, monkeypatch):
    monkeypatch.delenv("ALPACA_LIVE_TRADE", raising=False)
    fake = _FakeCLI(
        submit=[_ok({"id": "o6"})],
        get=[_ok({"status": "filled", "filled_avg_price": "2.00", "filled_qty": "1"})],
    )
    _wire(monkeypatch, fake)
    res = BrokerCLI().submit_and_fill("SPY260831C00640000", "buy", 1, "buy_to_open")
    assert res["status"] == "filled"


# --- get-by-client-id reconciliation (ambiguous submit failure) ----------------

def test_submit_reconciles_when_submit_errors(clock, monkeypatch):
    # `order submit` errors (ambiguous), but the order actually reached Alpaca.
    # We must recover its id by client-order-id, NOT resubmit (which duplicates).
    fake = _FakeCLI(
        submit=[_err("connection reset")],
        by_client=_ok({"id": "o-recon", "status": "accepted"}),
        get=[_ok({"status": "filled", "filled_avg_price": "1.10", "filled_qty": "1"})],
    )
    _wire(monkeypatch, fake)
    res = BrokerCLI(poll_timeout=45, poll_interval=2).submit_and_fill(
        "SPY260831C00640000", "buy", 1, "buy_to_open", client_order_id="cid-1")
    assert res["order_id"] == "o-recon" and res["status"] == "filled"
    assert fake.count("get-by-client-id") == 1
    assert fake.count("submit") == 1                 # exactly one submit — no duplicate


def test_submit_reconciles_when_no_id_returned(clock, monkeypatch):
    fake = _FakeCLI(
        submit=[_ok({})],                            # returned a body but no id
        by_client=_ok({"id": "o-recon2"}),
        get=[_ok({"status": "filled", "filled_avg_price": "1.00", "filled_qty": "1"})],
    )
    _wire(monkeypatch, fake)
    res = BrokerCLI(poll_timeout=45, poll_interval=2).submit_and_fill(
        "SPY260831C00640000", "buy", 1, "buy_to_open", client_order_id="cid-2")
    assert res["order_id"] == "o-recon2"


def test_submit_reconcile_miss_raises(clock, monkeypatch):
    # submit errors AND the order isn't found by client id -> it never landed;
    # raise so the caller books nothing (safe to not have a position).
    fake = _FakeCLI(submit=[_err("boom")], by_client=_err("not found"))
    _wire(monkeypatch, fake)
    with pytest.raises(BrokerError):
        BrokerCLI().submit_market("SPY260831C00640000", "buy", 1, "buy_to_open",
                                  client_order_id="cid-3")


def test_submit_no_client_id_does_not_reconcile(clock, monkeypatch):
    # Without a client id there's nothing to reconcile against -> just raise,
    # and never call get-by-client-id.
    fake = _FakeCLI(submit=[_err("boom")])
    _wire(monkeypatch, fake)
    with pytest.raises(BrokerError):
        BrokerCLI().submit_market("SPY260831C00640000", "buy", 1, "buy_to_open")
    assert fake.count("get-by-client-id") == 0


# --- verify_paper (alpaca doctor endpoint guard) -------------------------------

_DOCTOR_PAPER = "Connectivity:\n  Trading:  https://paper-api.alpaca.markets\n"
_DOCTOR_LIVE = "Connectivity:\n  Trading:  https://api.alpaca.markets\n"


def test_verify_paper_ok_on_paper_endpoint(monkeypatch):
    monkeypatch.delenv("ALPACA_LIVE_TRADE", raising=False)
    fake = _FakeCLI(doctor=_Proc(0, _DOCTOR_PAPER))
    _wire(monkeypatch, fake)
    BrokerCLI().verify_paper()                        # must not raise
    # the paper URL contains the substring "api.alpaca.markets" — the live regex
    # must NOT false-positive on it (that's the whole point of the boundary).


def test_verify_paper_refuses_live_endpoint(monkeypatch):
    monkeypatch.delenv("ALPACA_LIVE_TRADE", raising=False)
    fake = _FakeCLI(doctor=_Proc(0, _DOCTOR_LIVE))
    _wire(monkeypatch, fake)
    with pytest.raises(BrokerError, match="LIVE"):
        BrokerCLI().verify_paper()


def test_verify_paper_refuses_when_both_urls_present(monkeypatch):
    # If doctor output shows BOTH endpoints, live wins -> refuse (fail-safe). Guards
    # against a future regex "cleanup" silently turning this into fail-open.
    monkeypatch.delenv("ALPACA_LIVE_TRADE", raising=False)
    both = "live: https://api.alpaca.markets\n  Trading:  https://paper-api.alpaca.markets\n"
    fake = _FakeCLI(doctor=_Proc(0, both))
    _wire(monkeypatch, fake)
    with pytest.raises(BrokerError, match="LIVE"):
        BrokerCLI().verify_paper()


def test_verify_paper_refuses_on_empty_doctor(monkeypatch):
    monkeypatch.delenv("ALPACA_LIVE_TRADE", raising=False)
    fake = _FakeCLI(doctor=_Proc(0, ""))
    _wire(monkeypatch, fake)
    with pytest.raises(BrokerError, match="could not confirm"):
        BrokerCLI().verify_paper()


def test_verify_paper_refuses_when_endpoint_absent(monkeypatch):
    monkeypatch.delenv("ALPACA_LIVE_TRADE", raising=False)
    fake = _FakeCLI(doctor=_Proc(1, "✗ no credentials — run `alpaca profile login`"))
    _wire(monkeypatch, fake)
    with pytest.raises(BrokerError, match="could not confirm"):
        BrokerCLI().verify_paper()


def test_verify_paper_env_live_refuses_before_doctor(monkeypatch):
    fake = _FakeCLI()                                 # no doctor queued
    _wire(monkeypatch, fake)
    monkeypatch.setenv("ALPACA_LIVE_TRADE", "true")
    with pytest.raises(BrokerError, match="paper-only"):
        BrokerCLI().verify_paper()
    assert fake.calls == []                           # doctor never ran
