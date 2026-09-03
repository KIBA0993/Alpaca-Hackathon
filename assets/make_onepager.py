#!/usr/bin/env python3
"""Render docs/ONE_PAGER.md - the hackathon's required one-page write-up.

    python3 assets/make_onepager.py

The write-up must cover AI logic, risk gates, and the Alpaca infrastructure
implementation; those are the three numbered sections below and they are the
graded part, so keep them.

Result figures are pulled from the live paper account at build time - the same
source the cover and the deck use - so the three artifacts can never disagree.
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "ONE_PAGER.md"
PDF = ROOT / "docs" / "ONE_PAGER.pdf"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ACCT = "PA38HG4D9653"
REPO = "github.com/KIBA0993/Alpaca-Hackathon"
START = 100_000.0


def _env(path):
    d = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def book():
    e = _env(ROOT / ".env")
    h = {"APCA-API-KEY-ID": e["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": e["ALPACA_SECRET_KEY"]}

    def get(p):
        req = urllib.request.Request("https://paper-api.alpaca.markets" + p, headers=h)
        return json.load(urllib.request.urlopen(req, timeout=30))

    equity = float(get("/v2/account")["equity"])
    orders = get("/v2/orders?status=all&after=2026-08-28T00:00:00Z&limit=500&direction=asc")
    fills = [o for o in orders if o["status"] == "filled" and o.get("filled_at")]
    return {
        "equity": equity,
        "pct": (equity / START - 1) * 100,
        "sessions": len({o["filled_at"][:10] for o in fills}),
        "entries": sum(1 for o in fills if o["side"] == "buy"),
        "exits": sum(1 for o in fills if o["side"] == "sell"),
    }


TEMPLATE = """# Every Trade Provable. Every Loss Capped.

**Alpaca AI Trading Agents Hackathon — one-page write-up**  
Paper account `{acct}` · single-leg long 0DTE on SPY / QQQ / IWM · paper only, no live path in the repo.  
**Result: a fresh $100,000 account finished at ${equity:,.0f} ({pct:+.1f}%) across {sessions} trading sessions — {entries} entries, {exits} exits.**

An autonomous agent that scans, scores, gates, sizes, executes and manages its own book with
no human in the loop. Every control in it was validated against a year of real Alpaca OPRA
option bars — 264 sessions, 4,544 alerts — and what did not survive that testing is not in
the code. Repo: `{repo}`

## 1 · AI logic

A pure, unit-tested scorer reduces Alpaca 5-minute bars to one number from VWAP position,
opening-range break, RSI, EMA stack and relative volume, plus a proposed direction. Two
regime reads sit above it: a **half-opening-range noise band** (is this break larger than the
symbol's own morning noise?) and **leader breadth** — 5 of 8 mega-caps above their 20-day
average as of the last *completed* session, which only ever removes the opposed side.

Above that sits a **Claude reasoning layer scoped as a veto**. It argues bull and bear over
the scored facts and returns strict JSON, and it can only turn a *go* into a *no-go* — it can
never resurrect a trade the rules rejected. The deterministic path is therefore a strict
subset of the AI path, and the two are auditable against each other. A missing key or an
unparseable reply degrades to the deterministic gate and says so in the journal. The scored
sessions ran the deterministic gate; the veto ships tested and runnable via
`--decision-mode llm`.

## 2 · Risk gates

**Single-leg long options only: maximum loss is the premium paid, enforced by the instrument
rather than by code that has to remember.** On top of that, `src/risk.py` and
`src/execution.py` enforce, in order:

| gate | rule |
|---|---|
| one-lot guard | never a second open lot on a `(symbol, direction)` already held |
| one direction / underlying | no simultaneous call *and* put on the same symbol |
| entry dedup | 30 min, entry-anchored — a persistent score cannot stack the same name |
| adaptive sizing | target trimmed to the account's **real** options buying power, so an expensive contract sizes *down* instead of rejecting |
| session window | no entry after 15:00 ET; **hard flatten 15:50** — a 0DTE never reaches expiry |
| orphan sweep | any option position the agent did not open is flattened at startup and EOD |

Exits ladder in both directions: **+40% sells half** and trails the runner by 40% of its peak
gain; **−20% sells half, −40% the rest**; a 30-minute stop closes anything still under water;
15:50 flattens the book. **174 network-free tests** cover the scorer, the gates, the sizing
maths and the execution path.

## 3 · Alpaca infrastructure

**Every order is placed through the Alpaca CLI, not the SDK** (`src/broker_cli.py`): `alpaca
order submit`, then poll to a terminal state — so the agent books the *real broker fill*
rather than a quote, and cash P&L is buy-fill against sell-fill. Two safety belts: before
placing anything it verifies that **`alpaca doctor` resolves `paper-api.alpaca.markets`** and
refuses otherwise (the environment variable alone is not proof — a profile's `live_trade` can
route live), and on an ambiguous submit failure it reconciles by `--client-order-id` via
`alpaca order get-by-client-id` before it will ever resubmit, so a stalled order is never
duplicated or orphaned. A paper order becomes a position only when it actually **fills**,
and entries use a marketable limit to bound slippage.

Market data — bars, contract discovery, ATM quotes — comes from the Alpaca Market Data API.
The agent runs unattended as an **isolated Docker container** under a supervisor that starts
one session each weekday. Keys are injected at runtime, never baked into the image.

**Every scan appends one JSONL record** — score, each signal, band state, gate verdict, risk
verdict, order — to `logs/decisions-YYYY-MM-DD.jsonl`. Most agents show you the trades they
took; this one shows the trades it refused, and exactly why.

*Paper trading only. Hypothetical results; not investment advice.*
"""


PRINT_CSS = """
@page { size: Letter; margin: 12mm 14mm; }
* { box-sizing: border-box; }
body { font: 8.6pt/1.42 -apple-system, "Helvetica Neue", Arial, sans-serif;
       color: #14181f; margin: 0; }
h1 { font-size: 17pt; margin: 0 0 4px; letter-spacing: -.02em; }
h2 { font-size: 10.2pt; margin: 11px 0 4px; padding-top: 6px;
     border-top: 1px solid #dfe3ea; color: #8a6d1a; }
p { margin: 0 0 6px; }
strong { color: #000; }
code { font: 8pt "SF Mono", Menlo, monospace; background: #f2f4f7;
       padding: 0 3px; border-radius: 3px; }
table { border-collapse: collapse; width: 100%; margin: 6px 0; font-size: 8.1pt; }
td { border-top: 1px solid #e6e9ef; padding: 3px 6px; vertical-align: top; }
td:first-child { width: 27%; font-weight: 600; white-space: nowrap; }
thead { display: none; }
em { color: #444; }
"""


def render_pdf():
    """Print the write-up to a real one-page PDF, and verify it IS one page."""
    try:
        import markdown
    except ImportError:
        print("  (skipping PDF: pip install markdown)")
        return
    body = markdown.markdown(OUT.read_text(), extensions=["tables"])
    html = f"<!doctype html><meta charset='utf-8'><style>{PRINT_CSS}</style>{body}"
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        src = f.name
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={PDF}", f"file://{src}"],
                   check=True, capture_output=True)
    pathlib.Path(src).unlink()
    pages = "?"
    try:
        out = subprocess.run(["pdfinfo", str(PDF)], capture_output=True, text=True).stdout
        pages = next(l.split()[-1] for l in out.splitlines() if l.startswith("Pages:"))
    except Exception:
        pass
    flag = "" if pages == "1" else "   <-- NOT one page, trim the template"
    print(f"wrote {PDF}  —  {pages} page(s){flag}")


def main():
    b = book()
    OUT.write_text(TEMPLATE.format(acct=ACCT, repo=REPO, **b))
    words = len(OUT.read_text().split())
    print(f"wrote {OUT}  —  ${b['equity']:,.0f} ({b['pct']:+.1f}%), {b['sessions']} sessions, "
          f"{b['entries']} entries / {b['exits']} exits  ·  {words} words")
    render_pdf()


if __name__ == "__main__":
    sys.exit(main())
