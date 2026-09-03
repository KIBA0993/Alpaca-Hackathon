"""Order execution through the Alpaca CLI (`alpaca ...`), not the SDK.

The hackathon requires using Alpaca's MCP server or CLI. Market DATA (quotes,
contract discovery) stays on the SDK in marketdata.py; ORDER PLACEMENT — the
competition-critical, side-effectful path — goes through the CLI here.

The CLI reads ALPACA_API_KEY / ALPACA_SECRET_KEY from the environment (loaded from
.env by config.load_config) and DEFAULTS TO PAPER when using env keys. A subprocess
inherits this process's environment, so no keys are passed on the command line.

Robustness (a market order that stalls must not orphan a live position):
  * poll_fill tolerates a transient CLI blip mid-poll (keeps polling to the
    deadline) instead of aborting on the first hiccup;
  * on a genuine poll timeout, submit_and_fill CANCELS the order, then reads its
    true final state once more — so a fill that landed despite the timeout is
    still captured (including a PARTIAL fill, which the caller books at the actual
    filled_qty), and an un-filled order is never silently orphaned in OUR tracking
    (it comes back canceled/rejected, or we raise loudly). A broker-side race can
    still leave a `pending_cancel` whose remainder fills later; the caller
    reconciles the true held quantity after booking a partial to close that gap.
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import time
from typing import Optional

# Alpaca order lifecycle end states. `done_for_day`, `stopped`, `replaced` are
# terminal-ish states a DAY order can reach near the close; without them a poll
# would time out and orphan the order. (`cancelled` — double-L — is never emitted.)
TERMINAL = {"filled", "canceled", "rejected", "expired", "done_for_day",
            "stopped", "replaced"}

# The CLI routes to LIVE when ALPACA_LIVE_TRADE is set; unset/empty => paper. This
# repo is paper-only, so we refuse to place an order for ANY value that isn't
# explicitly falsey — safer than a fixed truthy allowlist (a value the CLI reads
# as live, e.g. "2" or "enabled", must not slip through).
_LIVE_FALSEY = {"", "0", "false", "no", "off", "n", "f"}

# The env var is only ONE of the ways the CLI can route live — the active
# profile's `live_trade` field does it too. The authoritative check is the
# endpoint `alpaca doctor` resolves. Match the LIVE URL only when it is NOT the
# tail of the PAPER URL ("...paper-api.alpaca.markets" contains "api.alpaca...").
_PAPER_ENDPOINT = "https://paper-api.alpaca.markets"
_LIVE_ENDPOINT_RE = re.compile(r"(?<![\w-])https://api\.alpaca\.markets")


class BrokerError(RuntimeError):
    pass


def _live_trade_enabled() -> bool:
    return (os.environ.get("ALPACA_LIVE_TRADE") or "").strip().lower() not in _LIVE_FALSEY


class BrokerCLI:
    def __init__(self, poll_timeout: float = 45.0, poll_interval: float = 2.0,
                 echo=None):
        self.poll_timeout = poll_timeout
        self.poll_interval = poll_interval
        self.echo = echo or (lambda msg: None)

    # ------------------------------------------------------------------ plumbing
    def _run(self, args: list[str]) -> dict | list | None:
        cmd = ["alpaca", *args]
        self.echo(" ".join(cmd))
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            raise BrokerError((p.stderr or p.stdout).strip() or f"alpaca exited {p.returncode}")
        out = p.stdout.strip()
        if not out:
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out

    # ---------------------------------------------------------------- paper guard
    def _doctor_text(self) -> str:
        """Raw `alpaca doctor` output. doctor exits non-zero when a check fails
        but still prints the resolved endpoint, so we DON'T gate on the exit code
        here — we parse the text."""
        cmd = ["alpaca", "doctor"]
        self.echo(" ".join(cmd))
        p = subprocess.run(cmd, capture_output=True, text=True)
        return f"{p.stdout or ''}\n{p.stderr or ''}"

    def verify_paper(self) -> None:
        """Prove the CLI resolves to the PAPER endpoint before ANY order. Both the
        env var AND the active profile's `live_trade` can route live, so the env
        check alone is not enough — `alpaca doctor` reports the resolved endpoint.
        Raises BrokerError unless paper is proven."""
        if _live_trade_enabled():
            raise BrokerError(
                "ALPACA_LIVE_TRADE is set to a live value — refusing (paper-only). "
                "Unset it (or set it to false).")
        out = self._doctor_text()
        if _LIVE_ENDPOINT_RE.search(out):
            raise BrokerError(
                "`alpaca doctor` resolves the LIVE trading endpoint — refusing. "
                "Switch to a paper profile and unset ALPACA_LIVE_TRADE.")
        if _PAPER_ENDPOINT not in out:
            raise BrokerError(
                "could not confirm the PAPER endpoint from `alpaca doctor` "
                "(no resolved trading endpoint — check credentials/profile). "
                "Refusing to place orders until paper is proven.")

    # -------------------------------------------------------------------- orders
    def submit_market(self, occ: str, side: str, qty: int, intent: str,
                      client_order_id: Optional[str] = None,
                      limit_price: Optional[float] = None) -> str:
        """Submit a DAY order for one option contract; return the order id.

        With `limit_price` it is a LIMIT order (used as a *marketable* limit to
        bound slippage/cost on multi-contract 0DTE orders); without it, a market
        order (the 1-lot behaviour)."""
        if _live_trade_enabled():
            # Seatbelt: this repo is paper-only. Refuse rather than route live.
            raise BrokerError(
                "ALPACA_LIVE_TRADE is set to a live value — refusing to place an "
                "order. This project is paper-only; unset ALPACA_LIVE_TRADE.")
        args = ["order", "submit", "--symbol", occ, "--qty", str(qty),
                "--side", side, "--time-in-force", "day",
                "--position-intent", intent]
        if limit_price is not None:
            args += ["--type", "limit", "--limit-price", f"{float(limit_price):.2f}"]
        else:
            args += ["--type", "market"]
        if client_order_id:
            args += ["--client-order-id", client_order_id]
        try:
            o = self._run(args)
        except BrokerError as exc:
            # Ambiguous failure (timeout / killed process): the order MIGHT have
            # reached Alpaca. Reconcile by client id before giving up, so we
            # neither orphan a live order nor blindly resubmit (a duplicate).
            oid = self._reconcile_client_id(client_order_id)
            if oid:
                self.echo(f"submit errored ({exc}) but the order exists — "
                          f"reconciled by client id -> {oid}")
                return oid
            raise
        oid = o.get("id") if isinstance(o, dict) else None
        if not oid:
            oid = self._reconcile_client_id(client_order_id)
            if oid:
                return oid
            raise BrokerError(f"submit returned no order id: {o!r}")
        return str(oid)

    def get_order(self, order_id: str) -> dict:
        o = self._run(["order", "get", "--order-id", order_id])
        if not isinstance(o, dict):
            raise BrokerError(f"order get returned {o!r}")
        return o

    def get_by_client_id(self, client_order_id: str) -> Optional[dict]:
        """Look an order up by the client-order-id we generated. Returns the order
        dict, or None if it isn't found (i.e. it never reached Alpaca)."""
        try:
            o = self._run(["order", "get-by-client-id",
                           "--client-order-id", client_order_id])
        except BrokerError:
            return None                      # not found / not yet visible
        return o if isinstance(o, dict) else None

    def _reconcile_client_id(self, client_order_id: Optional[str]) -> Optional[str]:
        if not client_order_id:
            return None
        found = self.get_by_client_id(client_order_id)
        return str(found["id"]) if found and found.get("id") else None

    def cancel_order(self, order_id: str) -> None:
        """Best-effort cancel. A 422 (already filled / not cancelable) is fine —
        the caller re-reads the true final state afterwards."""
        try:
            self._run(["order", "cancel", "--order-id", order_id])
        except BrokerError as exc:
            self.echo(f"cancel {order_id} not accepted (likely already terminal): {exc}")

    def poll_fill(self, order_id: str) -> dict:
        """Poll until the order is terminal or timeout. Returns the final order dict.

        A transient CLI error mid-poll does NOT abort — we keep polling to the
        deadline, because giving up early on a blip is exactly how a filled order
        gets orphaned. Only a real timeout raises."""
        deadline = time.time() + self.poll_timeout
        last: dict = {}
        last_err: Optional[str] = None
        while time.time() < deadline:
            try:
                last = self.get_order(order_id)
                last_err = None
            except BrokerError as exc:            # transient blip — keep polling
                last_err = str(exc)
                time.sleep(self.poll_interval)
                continue
            if (last.get("status") or "").lower() in TERMINAL:
                return last
            time.sleep(self.poll_interval)
        raise BrokerError(
            f"order {order_id} not terminal after {self.poll_timeout}s "
            f"(last status={last.get('status')}"
            + (f", last error={last_err}" if last_err else "") + ")")

    def submit_and_fill(self, occ: str, side: str, qty: int, intent: str,
                        client_order_id: Optional[str] = None,
                        limit_price: Optional[float] = None) -> dict:
        """Submit and block until terminal. Returns
        {order_id, status, fill_price, filled_qty}.

        On a poll timeout we cancel and reconcile once more, so our tracking never
        holds a submitted order in limbo: either it filled (captured), or it comes
        back non-filled (book nothing), or its final state is unreadable and we
        raise for a human to check."""
        oid = self.submit_market(occ, side, qty, intent, client_order_id,
                                 limit_price=limit_price)
        try:
            o = self.poll_fill(oid)
        except BrokerError as exc:
            self.echo(f"poll timed out for {oid} ({exc}); canceling and reconciling")
            self.cancel_order(oid)                # best-effort; may already be filled
            try:
                o = self.get_order(oid)           # the TRUE final state after cancel
            except BrokerError as exc2:
                # Could not even read it back — surface loudly, book nothing.
                raise BrokerError(
                    f"order {oid} timed out and its final state is unknown "
                    f"({exc2}); check the account manually") from exc2
        return self._summarize(oid, o)

    @staticmethod
    def _summarize(order_id: str, o: dict) -> dict:
        status = (o.get("status") or "").lower()
        fill = o.get("filled_avg_price")
        fq = o.get("filled_qty")
        try:
            filled_qty = int(float(fq))          # Alpaca serializes qty as a string
        except (TypeError, ValueError):
            filled_qty = 0
        return {"order_id": order_id, "status": status,
                "fill_price": float(fill) if fill else None,
                "filled_qty": filled_qty}

    def options_buying_power(self) -> Optional[float]:
        """The account's options buying power (float), or None if unreadable — so
        the aggregate exposure cap can be set from the REAL account, not a guess."""
        acct = self._run(["account", "get"])
        if not isinstance(acct, dict):
            return None
        for k in ("options_buying_power", "buying_power"):
            v = acct.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return None

    def list_option_positions(self) -> dict:
        """{occ_symbol: signed_qty} for currently-held OPTION positions, so the
        agent can flatten anything it isn't tracking (orphan guard). Returns {}
        when the account has none; raises only on a hard CLI failure."""
        out: dict = {}
        rows = self._run(["position", "list"])
        if not isinstance(rows, list):
            return out
        for r in rows:
            if not isinstance(r, dict):
                continue
            occ = r.get("symbol") or r.get("asset_id")
            cls = str(r.get("asset_class") or r.get("class") or "")
            # option OCC symbols are long (underlying+YYMMDD+C/P+strike); also
            # accept an explicit option asset_class if the CLI provides one.
            looks_option = ("option" in cls.lower()) or (occ and len(str(occ)) > 15)
            if not occ or not looks_option:
                continue
            try:
                q = int(float(r.get("qty") or 0))
            except (TypeError, ValueError):
                q = 0
            if q:
                out[str(occ)] = q
        return out
