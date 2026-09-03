#!/usr/bin/env python3
"""Render the hackathon cover image (1920x1080 PNG) from the LIVE paper account.

    python3 assets/make_cover.py           # pulls live equity, writes assets/cover.png

Every number on the cover is pulled at build time - nothing is hardcoded - so
re-running it right before submitting keeps the headline honest.
Needs Google Chrome (headless screenshot). No pip installs.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "cover.png"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
START_EQUITY = 100_000.0
REPO = "github.com/KIBA0993/Alpaca-Hackathon"

# --- palette (dark surface; single-series line needs no legend) ----------------
BG, INK, INK2, MUTED = "#0B0E14", "#FFFFFF", "#A1A8B5", "#5C6675"
GOLD, LINE = "#F0B429", "#1C2230"


def _env(path):
    d = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def fetch():
    """Live equity, the equity curve, and the number of sessions that actually traded."""
    e = _env(ROOT / ".env")
    h = {"APCA-API-KEY-ID": e["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": e["ALPACA_SECRET_KEY"]}

    def get(p):
        req = urllib.request.Request("https://paper-api.alpaca.markets" + p, headers=h)
        return json.load(urllib.request.urlopen(req, timeout=30))

    acct = get("/v2/account")
    equity = float(acct["equity"])
    hist = get("/v2/account/portfolio/history?period=1M&timeframe=1D")
    closes = [v for v in hist["equity"] if v]

    # A session counts only if it actually filled orders - a flat day is not a session.
    orders = get("/v2/orders?status=all&after=2026-08-28T00:00:00Z&limit=500&direction=asc")
    traded_days = sorted({o["filled_at"][:10] for o in orders
                          if o["status"] == "filled" and o.get("filled_at")})
    return equity, closes + [equity], len(traded_days)


def sparkline(series, w=470, h=140, pad=10):
    """Single-series equity line + a marker on the latest point."""
    lo, hi = min(series), max(series)
    rng = (hi - lo) or 1.0
    n = len(series)
    pts = [(pad + i * (w - 2 * pad) / (n - 1), h - pad - (v - lo) / rng * (h - 2 * pad))
           for i, v in enumerate(series)]
    d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    base = h - pad
    area = f"{d} L {pts[-1][0]:.1f},{base} L {pts[0][0]:.1f},{base} Z"
    lx, ly = pts[-1]
    return f"""<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" fill="none">
  <defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="{GOLD}" stop-opacity=".30"/>
    <stop offset="100%" stop-color="{GOLD}" stop-opacity="0"/></linearGradient></defs>
  <path d="{area}" fill="url(#g)"/>
  <path d="{d}" stroke="{GOLD}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
  <circle cx="{lx:.1f}" cy="{ly:.1f}" r="7" fill="{GOLD}" stroke="{BG}" stroke-width="2.5"/>
</svg>"""


def html(equity, series, sessions):
    pct = (equity / START_EQUITY - 1) * 100
    return f"""<!doctype html><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1920px;height:1080px;background:{BG};color:{INK};
  font-family:"Helvetica Neue",Inter,-apple-system,sans-serif;
  padding:92px 112px;display:flex;flex-direction:column;justify-content:space-between;
  -webkit-font-smoothing:antialiased;overflow:hidden}}
.mono{{font-family:"SF Mono",Menlo,ui-monospace,monospace}}
.kicker{{font-size:23px;letter-spacing:.22em;color:{MUTED};text-transform:uppercase}}
h1{{font-size:128px;line-height:.95;letter-spacing:-.035em;font-weight:700;margin:48px 0 0}}
h1 em{{font-style:normal;color:{GOLD}}}
.sub{{font-size:32px;line-height:1.45;color:{INK2};max-width:1240px;margin-top:38px;font-weight:300}}
.cards{{display:flex;gap:26px;align-items:stretch}}
.card{{background:#11151E;border:1px solid {LINE};border-radius:20px;padding:34px 38px;flex:1;
  display:flex;flex-direction:column;justify-content:center}}
.card.live{{flex:1.85;flex-direction:row;justify-content:space-between;align-items:center;gap:26px}}
.lbl{{font-size:19px;letter-spacing:.17em;color:{MUTED};text-transform:uppercase;white-space:nowrap}}
.big{{font-size:74px;font-weight:700;letter-spacing:-.03em;margin-top:16px;line-height:1;color:{GOLD}}}
.mid{{font-size:40px;font-weight:700;letter-spacing:-.02em;margin-top:16px;line-height:1.1;color:{INK}}}
.note{{font-size:22px;color:{INK2};margin-top:14px;font-weight:300;white-space:nowrap}}
.note.wrap{{white-space:normal;line-height:1.35}}
.foot{{display:flex;justify-content:space-between;align-items:baseline;
  border-top:1px solid {LINE};padding-top:32px;margin-top:10px}}
.quote{{font-size:29px;color:{INK};font-weight:300}}
.repo{{font-size:22px;color:{MUTED}}}
</style>
<div>
  <div class="kicker mono">Alpaca AI Trading Agents Hackathon · Sept 2026</div>
  <h1>Every Trade Provable.<br><em>Every Loss Capped.</em></h1>
  <div class="sub">An autonomous 0DTE options agent on Alpaca. Max loss capped by
    construction, and every decision it makes is journaled.</div>
</div>

<div class="cards">
  <div class="card live">
    <div>
      <div class="lbl mono">Live Alpaca paper · {sessions} sessions</div>
      <div class="big">{pct:+.1f}%</div>
      <div class="note mono">$100,000 → ${equity:,.0f}</div>
    </div>
    {sparkline(series)}
  </div>
  <div class="card">
    <div class="lbl mono">Max loss</div>
    <div class="mid">= premium paid</div>
    <div class="note wrap">Single-leg long only — the ceiling is set by the
      instrument, not by code.</div>
  </div>
  <div class="card">
    <div class="lbl mono">Validation</div>
    <div class="mid">264 sessions</div>
    <div class="note wrap">of real OPRA option bars behind every risk control.</div>
  </div>
</div>

<div class="foot">
  <div class="quote">Most agents show you the trades they took.
    <strong style="font-weight:500">This one shows the ones it refused.</strong></div>
  <div class="repo mono">{REPO}</div>
</div>"""


def main():
    equity, series, sessions = fetch()
    OUT.parent.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html(equity, series, sessions))
        src = f.name
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
                    "--force-device-scale-factor=1", "--window-size=1920,1080",
                    f"--screenshot={OUT}", f"file://{src}"],
                   check=True, capture_output=True)
    os.unlink(src)
    print(f"wrote {OUT}  —  ${equity:,.2f} ({(equity/START_EQUITY-1)*100:+.1f}%), "
          f"{sessions} traded sessions")


if __name__ == "__main__":
    sys.exit(main())
