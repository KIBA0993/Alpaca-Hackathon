#!/usr/bin/env python3
"""Round-trip smoke test through the Alpaca CLI: buy one ATM 0DTE option, then
sell it to close. Proves the competition-critical path (place + sell an OPTION on
the paper account) AND satisfies the "must use Alpaca's MCP server or CLI" rule --
every account / data / order call below is the `alpaca` CLI, not the SDK.

WHY A SEPARATE SCRIPT: the agent's Executor fires an order but never waits for the
fill or captures the real fill price. A "can I actually trade?" test must confirm
the buy reaches `filled`, read the fill price, then close and read that fill too.

SAFE BY DEFAULT:
  * with no flags it runs PREFLIGHT ONLY (auth, options level, buying power, market
    clock, resolve an ATM contract + quote) and places NOTHING.
  * --dry-run additionally asks the CLI to validate the order body (still no order).
  * --live actually places the buy+sell -- and refuses unless the market is open.
Auth: the CLI reads ALPACA_API_KEY / ALPACA_SECRET_KEY from the environment and
defaults to PAPER. Set them (or `alpaca profile login`) before running.

Usage:
  python smoke_trade.py                 # preflight only (weekend-safe)
  python smoke_trade.py --dry-run       # + validate the order body via the CLI
  python smoke_trade.py --live --yes    # Monday, market open: real paper round trip
Flags: --symbol SPY  --qty 1
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import time
from datetime import date, datetime, timezone

from src.broker_cli import BrokerCLI, BrokerError
from src.config import ROOT, _load_dotenv


def run(args: list[str], *, check: bool = True) -> dict | list | None:
    """Run an `alpaca` CLI command and parse its JSON stdout. Echoes the command."""
    cmd = ["alpaca", *args]
    print(f"  $ {' '.join(cmd)}")
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        msg = (p.stderr or p.stdout).strip()
        if check:
            raise SystemExit(f"CLI error ({p.returncode}): {msg}")
        print(f"    ! {msg}")
        return None
    out = p.stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out  # non-JSON (rare); return raw


def preflight(symbol: str) -> dict:
    print("\n[0] verify PAPER endpoint (alpaca doctor)")
    try:
        BrokerCLI().verify_paper()
        print("    ✓ CLI resolves to the paper endpoint (env + profile checked)")
    except BrokerError as exc:
        raise SystemExit(f"    !! {exc}")

    print("\n[1] account + options entitlement (alpaca CLI)")
    acct = run(["account", "get"])
    level = acct.get("options_trading_level", acct.get("options_approved_level"))
    status = acct.get("status")
    bp = acct.get("buying_power")
    obp = acct.get("options_buying_power")
    print(f"    status={status}  options_trading_level={level}  "
          f"buying_power={bp}  options_buying_power={obp}")
    if status != "ACTIVE":
        print(f"    !! account status is {status}, not ACTIVE.")
    if level is None or int(level) < 1:
        raise SystemExit(
            "    !! options trading is NOT enabled on this account "
            f"(level={level}). Buying long calls/puts needs LEVEL 2. "
            "Enable options (Level 2) in the Alpaca paper dashboard, then retry.")
    if int(level) < 2:
        print(f"    !! options level {level}: covered/cash-secured only. Long "
              "single-leg calls/puts need LEVEL 2 -- a naked long buy will reject.")

    print("\n[2] market clock (alpaca CLI)")
    clock = run(["clock"])
    is_open = bool(clock.get("is_open"))
    print(f"    is_open={is_open}  next_open={clock.get('next_open')}  "
          f"next_close={clock.get('next_close')}")

    print(f"\n[3] spot for {symbol} (alpaca CLI)")
    q = run(["data", "latest-quote", "--symbol", symbol])
    quote = (q.get("quote") if isinstance(q, dict) else None) or q
    bid = float(quote.get("bp") or quote.get("bid_price") or 0)
    ask = float(quote.get("ap") or quote.get("ask_price") or 0)
    spot = (bid + ask) / 2 if bid and ask else (ask or bid)
    if not spot:
        raise SystemExit("    !! could not read a spot price for the underlying.")
    print(f"    spot≈{spot:.2f}  (bid {bid} / ask {ask})")

    print(f"\n[4] resolve ATM 0DTE-or-soonest call for {symbol} (alpaca CLI)")
    today = date.today().isoformat()
    contracts = run([
        "option", "contracts", "--underlying-symbols", symbol,
        "--type", "call", "--expiration-date-gte", today,
        "--strike-price-gte", f"{spot*0.97:.2f}",
        "--strike-price-lte", f"{spot*1.03:.2f}", "--limit", "500"])
    rows = contracts.get("option_contracts") if isinstance(contracts, dict) else contracts
    if not rows:
        raise SystemExit("    !! no contracts returned in the ATM window.")
    soonest = min(r["expiration_date"] for r in rows)
    near = [r for r in rows if r["expiration_date"] == soonest]
    best = min(near, key=lambda r: abs(float(r["strike_price"]) - spot))
    occ = best["symbol"]
    print(f"    expiry={soonest} (0DTE if today)  strike={best['strike_price']}  {occ}")

    print(f"\n[5] quote the chosen contract (alpaca CLI)")
    oq = run(["data", "option", "latest-quotes", "--symbols", occ])
    node = oq.get("quotes", oq) if isinstance(oq, dict) else oq
    ov = node.get(occ, node) if isinstance(node, dict) else {}
    obid = float(ov.get("bp") or ov.get("bid_price") or 0)
    oask = float(ov.get("ap") or ov.get("ask_price") or 0)
    print(f"    bid={obid}  ask={oask}  est cost/contract=${oask*100:.0f}")
    return {"occ": occ, "is_open": is_open, "ask": oask, "bid": obid,
            "expiry": soonest, "strike": best["strike_price"]}


def poll_fill(order_id: str, timeout: float = 45.0) -> dict:
    """Poll `alpaca order get` until the order is terminal or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        o = run(["order", "get", "--order-id", order_id], check=False) or {}
        st = o.get("status")
        print(f"    order {order_id[:8]}… status={st} filled_qty={o.get('filled_qty')}")
        if st in {"filled", "canceled", "rejected", "expired"}:
            return o
        time.sleep(2)
    raise SystemExit(f"    !! order {order_id} did not reach a terminal state in {timeout}s")


def submit(occ: str, side: str, qty: int, intent: str) -> str:
    coid = f"smoke-{side}-{int(datetime.now(timezone.utc).timestamp())}"
    o = run(["order", "submit", "--symbol", occ, "--qty", str(qty),
             "--side", side, "--type", "market", "--time-in-force", "day",
             "--position-intent", intent, "--client-order-id", coid])
    oid = o.get("id")
    print(f"    submitted {side} {occ} qty={qty} -> order id {oid}")
    return oid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--qty", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true",
                    help="validate the order body via the CLI, place nothing")
    ap.add_argument("--live", action="store_true",
                    help="actually place buy+sell (market must be open)")
    ap.add_argument("--yes", action="store_true", help="confirm --live")
    args = ap.parse_args()

    # Load .env into the environment so the `alpaca` CLI subprocess inherits the
    # paper keys (the agent does this via load_config; this standalone script must
    # do it itself, or `alpaca doctor` sees no credentials).
    _load_dotenv(ROOT / ".env")

    print("=" * 68)
    print("ALPACA CLI ROUND-TRIP SMOKE TEST  (buy ATM 0DTE option -> sell to close)")
    print("=" * 68)
    info = preflight(args.symbol)

    if args.dry_run:
        print("\n[6] DRY-RUN order body (alpaca CLI --dry-run, nothing submitted)")
        run(["order", "submit", "--symbol", info["occ"], "--qty", str(args.qty),
             "--side", "buy", "--type", "market", "--time-in-force", "day",
             "--position-intent", "buy_to_open", "--dry-run"], check=False)

    if not args.live:
        print("\n[OK] PREFLIGHT PASSED — nothing was placed.")
        print("     Weekend: this is as far as it goes (options fill only during RTH).")
        print("     Monday 9:30–16:00 ET, run:  python smoke_trade.py --live --yes")
        return

    if not args.yes:
        raise SystemExit("\n--live requires --yes (this places real paper orders).")
    if not info["is_open"]:
        raise SystemExit("\n!! market is CLOSED — option market orders won't fill. "
                         "Run during RTH (Mon 9:30–16:00 ET).")

    print("\n[6] BUY to open (alpaca CLI)")
    buy_id = submit(info["occ"], "buy", args.qty, "buy_to_open")
    buy = poll_fill(buy_id)
    if buy.get("status") != "filled":
        raise SystemExit(f"!! BUY did not fill (status={buy.get('status')}). Stopping; no position to close.")
    buy_px = float(buy.get("filled_avg_price") or 0)
    print(f"    BUY FILLED @ {buy_px}")

    print("\n[7] positions (alpaca CLI)")
    run(["position", "list"], check=False)

    print("\n[8] SELL to close (alpaca CLI)")
    sell_id = submit(info["occ"], "sell", args.qty, "sell_to_close")
    sell = poll_fill(sell_id)
    if sell.get("status") != "filled":
        raise SystemExit(f"!! SELL did not fill (status={sell.get('status')}). "
                         "You may still hold the position — close it manually.")
    sell_px = float(sell.get("filled_avg_price") or 0)
    print(f"    SELL FILLED @ {sell_px}")

    pnl = (sell_px - buy_px) * 100 * args.qty
    print("\n" + "=" * 68)
    print(f"ROUND TRIP OK — bought @ {buy_px}, sold @ {sell_px}, "
          f"realized ${pnl:+.2f} on {args.qty} contract(s).")
    print("Place + sell both confirmed through the Alpaca CLI. You're ready.")
    print("=" * 68)


if __name__ == "__main__":
    main()
