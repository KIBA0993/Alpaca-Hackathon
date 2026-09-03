#!/usr/bin/env python3
"""Freeze the live paper account into assets/snapshot.json for the web demo.

    python3 assets/make_snapshot.py

The hosted demo (streamlit_app.py) must run on a machine with NO Alpaca keys, so
it reads this file instead of the API. Everything in it is pulled live at build
time - equity, the curve, per-session P&L, fill counts - by the SAME logic the
cover and the deck use, so all four artifacts can never disagree.

It also embeds the decision journal (logs/*.jsonl), which is what the demo is
really for: the trades the agent REFUSED are only visible in that record.
"""
import json
import pathlib
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "snapshot.json"
START_EQUITY = 100_000.0
SINCE = "2026-08-28T00:00:00Z"


def _env(path):
    d = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def _api(path, headers):
    req = urllib.request.Request("https://paper-api.alpaca.markets" + path, headers=headers)
    return json.load(urllib.request.urlopen(req, timeout=30))


def _all_orders(headers):
    """Every order since SINCE, paginated. The endpoint caps a page at 500 and
    silently truncates, so a single call can under-report the fill counts."""
    out, after, seen = [], SINCE, set()
    while True:
        q = urllib.parse.urlencode({"status": "all", "after": after,
                                    "limit": 500, "direction": "asc"})
        page = _api("/v2/orders?" + q, headers)
        fresh = [o for o in page if o["id"] not in seen]
        if not fresh:
            break
        out.extend(fresh)
        seen.update(o["id"] for o in fresh)
        if len(page) < 500:
            break
        after = max(o["submitted_at"] for o in fresh)
    return out


def fetch():
    e = _env(ROOT / ".env")
    h = {"APCA-API-KEY-ID": e["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": e["ALPACA_SECRET_KEY"]}

    acct = _api("/v2/account", h)
    equity = float(acct["equity"])
    hist = _api("/v2/account/portfolio/history?period=1M&timeframe=1D", h)
    orders = _all_orders(h)

    fills = [o for o in orders if o["status"] == "filled" and o.get("filled_at")]
    by_day = {}
    for o in fills:
        d = o["filled_at"][:10]
        r = by_day.setdefault(d, {"day": d, "buys": 0, "sells": 0, "contracts": 0,
                                  "symbols": set()})
        r["buys" if o["side"] == "buy" else "sells"] += 1
        r["contracts"] += int(o["filled_qty"])
        r["symbols"].add(o.get("symbol", "")[:3])
    sessions = [by_day[d] for d in sorted(by_day)]

    # Realised P&L per traded session - the non-zero daily rows, plus today's if
    # it has not settled into the history yet. Same rule as assets/make_deck.js.
    pls = [v for v, eq in zip(hist["profit_loss"], hist["equity"])
           if eq and abs(v) > 0.005]
    today = equity - float(acct["last_equity"])
    if len(pls) < len(sessions) and abs(today) > 0.005:
        pls.append(today)
    for i, s in enumerate(sessions):
        s["pnl"] = pls[i] if i < len(pls) else 0.0
        s["symbols"] = sorted(x for x in s["symbols"] if x)

    # The curve is built FROM the sessions, so its point count can never drift
    # from the label count (the bug that made the deck unopenable).
    run = START_EQUITY
    curve = [START_EQUITY]
    for s in sessions:
        run += s["pnl"]
        curve.append(run)

    return {
        "account": "PA38HG4D9653",
        "start_equity": START_EQUITY,
        "equity": equity,
        "pct": (equity / START_EQUITY - 1) * 100,
        "sessions": sessions,
        "curve": curve,
        "labels": ["Start"] + [s["day"] for s in sessions],
        "entries": sum(1 for o in fills if o["side"] == "buy"),
        "exits": sum(1 for o in fills if o["side"] == "sell"),
        "orders_seen": len(orders),
    }


def journal():
    """Every decision record in logs/, newest file last. This is the audit trail
    the demo exists to show; it contains no keys, no account ids, no PII."""
    d = ROOT / "logs"
    recs = []
    for f in sorted(d.glob("*.jsonl")) if d.exists() else []:
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return recs


def main():
    snap = fetch()
    snap["journal"] = journal()
    snap["config"] = json.loads((ROOT / "config.json").read_text())
    snap["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    OUT.write_text(json.dumps(snap, indent=1, default=str) + "\n")
    print(f"{OUT.relative_to(ROOT)}: ${snap['equity']:,.0f} ({snap['pct']:+.1f}%), "
          f"{len(snap['sessions'])} sessions, {snap['entries']} entries / "
          f"{snap['exits']} exits, {len(snap['journal'])} journal records")


if __name__ == "__main__":
    main()
