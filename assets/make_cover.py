#!/usr/bin/env python3
"""Render the hackathon cover image (1920x1080 PNG) from the LIVE paper account.

    python assets/make_cover.py            # pulls live equity, writes assets/cover.png

Re-run it right before submitting so the headline number matches the account.
Needs Google Chrome (headless screenshot) — no extra pip installs.
"""
import datetime
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

# --- palette (dark surface; single-series line needs no legend) ----------------
BG, INK, INK2, MUTED = "#0B0E14", "#FFFFFF", "#A1A8B5", "#5C6675"
GOLD, RED, LINE = "#F0B429", "#E5484D", "#1C2230"


def _env(path):
    d = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def fetch():
    e = _env(ROOT / ".env")
    h = {"APCA-API-KEY-ID": e["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": e["ALPACA_SECRET_KEY"]}
    def get(p):
        req = urllib.request.Request("https://paper-api.alpaca.markets" + p, headers=h)
        return json.load(urllib.request.urlopen(req, timeout=30))
    acct = get("/v2/account")
    hist = get("/v2/account/portfolio/history?period=1W&timeframe=1D")
    closes = [v for v in hist["equity"] if v]
    equity = float(acct["equity"])
    return equity, closes + [equity]


def sparkline(series, w=520, h=150, pad=10):
    """Single-series equity line + a marker on the live point."""
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
    <stop offset="0%" stop-color="{GOLD}" stop-opacity=".28"/>
    <stop offset="100%" stop-color="{GOLD}" stop-opacity="0"/></linearGradient></defs>
  <path d="{area}" fill="url(#g)"/>
  <path d="{d}" stroke="{GOLD}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
  <circle cx="{lx:.1f}" cy="{ly:.1f}" r="7" fill="{GOLD}" stroke="{BG}" stroke-width="2.5"/>
</svg>"""


def html(equity, series):
    pct = (equity / START_EQUITY - 1) * 100
    stamp = datetime.datetime.now().strftime("%b %-d, %Y")
    return f"""<!doctype html><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1920px;height:1080px;background:{BG};color:{INK};
  font-family:"Helvetica Neue",Inter,-apple-system,sans-serif;
  padding:96px 112px;display:flex;flex-direction:column;justify-content:space-between;
  -webkit-font-smoothing:antialiased;overflow:hidden}}
.mono{{font-family:"SF Mono",Menlo,ui-monospace,monospace}}
.kicker{{font-size:23px;letter-spacing:.22em;color:{MUTED};text-transform:uppercase}}
h1{{font-size:132px;line-height:.94;letter-spacing:-.035em;font-weight:700;margin:52px 0 0}}
h1 em{{font-style:normal;color:{GOLD}}}
.sub{{font-size:33px;line-height:1.45;color:{INK2};max-width:1180px;margin-top:40px;font-weight:300}}
.cards{{display:flex;gap:30px;align-items:stretch}}
.card{{background:#11151E;border:1px solid {LINE};border-radius:20px;padding:38px 42px;flex:1}}
.card.live{{flex:1.6;display:flex;justify-content:space-between;align-items:center;gap:30px}}
.lbl{{font-size:19px;letter-spacing:.17em;color:{MUTED};text-transform:uppercase;white-space:nowrap}}
.big{{font-size:76px;font-weight:700;letter-spacing:-.03em;margin-top:18px;line-height:1}}
.note{{font-size:23px;color:{INK2};margin-top:16px;font-weight:300;white-space:nowrap}}
.foot{{display:flex;justify-content:space-between;align-items:baseline;
  border-top:1px solid {LINE};padding-top:34px;margin-top:12px}}
.quote{{font-size:31px;color:{INK};font-weight:300;font-style:italic}}
.repo{{font-size:22px;color:{MUTED}}}
</style>
<div>
  <div class="kicker mono">Alpaca AI Trading Agents Hackathon · Sept 2026</div>
  <h1>The Agent That<br><em>Failed Its Own Backtest</em></h1>
  <div class="sub">Defined-risk 0DTE options on Alpaca. Max loss fixed by the
    instrument — not by code that has to remember.</div>
</div>

<div class="cards">
  <div class="card">
    <div class="lbl mono">Backtest verdict · 248 sessions</div>
    <div class="big" style="color:{RED}">−$8<span style="font-size:36px;color:{INK2};
      font-weight:300"> / trade</span></div>
    <div class="note">49.4% directional hit rate. Below a coin flip. No edge.</div>
  </div>
  <div class="card live">
    <div>
      <div class="lbl mono">Live Alpaca paper · 4 sessions</div>
      <div class="big" style="color:{GOLD}">{pct:+.1f}%</div>
      <div class="note mono">$100,000 → ${equity:,.0f}</div>
    </div>
    {sparkline(series)}
  </div>
</div>

<div class="foot">
  <div class="quote">“We won't call that edge.”</div>
  <div class="repo mono">github.com/KIBA0993/defined-risk-0dte-agent</div>
</div>"""


def main():
    equity, series = fetch()
    OUT.parent.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html(equity, series))
        src = f.name
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
                    "--force-device-scale-factor=1", "--window-size=1920,1080",
                    f"--screenshot={OUT}", f"file://{src}"],
                   check=True, capture_output=True)
    os.unlink(src)
    print(f"wrote {OUT}  —  equity ${equity:,.2f} ({(equity/START_EQUITY-1)*100:+.1f}%)")


if __name__ == "__main__":
    sys.exit(main())
