"""Interactive demo for the Alpaca AI Trading Agents Hackathon submission.

    streamlit run streamlit_app.py

This is the hosted prototype behind the submission's Application URL. It does
three things a repo cannot:

  1. shows the live paper result (equity curve, per-session P&L, fill counts),
  2. lets you DRIVE THE REAL GATE - the sliders call src/gate.py and src/risk.py
     directly, so what you see is the agent's own code deciding, not a mock-up,
  3. shows the decision journal, including the trades the agent REFUSED.

It needs no API keys: account figures come from assets/snapshot.json, frozen
from the live account by assets/make_snapshot.py at build time. Supply Alpaca
paper keys (env or .streamlit/secrets.toml) and it will refresh them live.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from datetime import datetime, time as dtime, timedelta

import streamlit as st

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.execution import Executor, Position          # noqa: E402
from src.gate import RulesGate                        # noqa: E402
from src.risk import RiskManager, RiskState           # noqa: E402

BG, CARD, LINE = "#0B0E14", "#141922", "#232A38"
GOLD, RED, INK, INK2, MUTED = "#F0B429", "#E5484D", "#FFFFFF", "#A1A8B5", "#6B7686"
REPO = "https://github.com/KIBA0993/Alpaca-Hackathon"

st.set_page_config(page_title="Every Trade Provable", page_icon="📓",
                   layout="wide", initial_sidebar_state="collapsed")

st.markdown(f"""<style>
.stApp {{ background:{BG}; }}
#MainMenu, footer, header {{ visibility:hidden; }}
.block-container {{ padding-top:2.2rem; max-width:1250px; }}
h1,h2,h3,h4 {{ color:{INK}; letter-spacing:-.02em; }}
p, li, label, .stMarkdown {{ color:{INK2}; }}
.kick {{ font-family:ui-monospace,Menlo,monospace; font-size:.72rem; letter-spacing:.2em;
        text-transform:uppercase; color:{MUTED}; }}
.hero {{ font-size:2.6rem; font-weight:700; line-height:1.05; color:{INK}; margin:.3rem 0 .1rem; }}
.hero em {{ font-style:normal; color:{GOLD}; }}
.card {{ background:{CARD}; border:1px solid {LINE}; border-radius:14px; padding:1.1rem 1.3rem; }}
.card .lbl {{ font-family:ui-monospace,Menlo,monospace; font-size:.66rem; letter-spacing:.14em;
             text-transform:uppercase; color:{MUTED}; }}
.card .val {{ font-size:2.0rem; font-weight:700; color:{INK}; line-height:1.15; margin-top:.25rem; }}
.card .val.gold {{ color:{GOLD}; }}
.card .note {{ font-size:.82rem; color:{INK2}; margin-top:.2rem; }}
.verdict {{ border-radius:14px; padding:1.05rem 1.3rem; border:1px solid; }}
.verdict.go {{ background:rgba(240,180,41,.09); border-color:{GOLD}; }}
.verdict.no {{ background:rgba(229,72,77,.08); border-color:{RED}; }}
.verdict .big {{ font-size:1.5rem; font-weight:700; }}
.verdict.go .big {{ color:{GOLD}; }}
.verdict.no .big {{ color:{RED}; }}
.verdict .why {{ font-family:ui-monospace,Menlo,monospace; font-size:.86rem; color:{INK};
                margin-top:.4rem; line-height:1.5; }}
.step {{ font-family:ui-monospace,Menlo,monospace; font-size:.83rem; color:{INK2};
        padding:.28rem 0; border-bottom:1px solid {LINE}; }}
.step b {{ color:{INK}; font-weight:600; }}
.pass {{ color:{GOLD}; }} .fail {{ color:{RED}; }} .skip {{ color:{MUTED}; }}
.stTabs [data-baseweb="tab-list"] {{ gap:.35rem; border-bottom:1px solid {LINE}; }}
.stTabs [data-baseweb="tab"] {{ color:{MUTED}; font-weight:600; }}
.stTabs [aria-selected="true"] {{ color:{GOLD} !important; }}
div[data-testid="stMetricValue"] {{ color:{INK}; }}
code {{ color:{GOLD}; }}
small {{ color:{MUTED}; }}
</style>""", unsafe_allow_html=True)


def md(s: str) -> str:
    """Inline markdown -> HTML. Streamlit does NOT process markdown inside a raw
    HTML block, so every string that lands in one has to be converted here."""
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", s)
    return re.sub(r"`([^`]+?)`", r"<code>\1</code>", s)


# ------------------------------------------------------------------ data --
@st.cache_data(show_spinner=False)
def snapshot() -> dict:
    p = ROOT / "assets" / "snapshot.json"
    if not p.exists():
        st.error("assets/snapshot.json is missing — run `python3 assets/make_snapshot.py`.")
        st.stop()
    return json.loads(p.read_text())


def _secret(name: str):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name)


@st.cache_data(ttl=60, show_spinner="Querying the Alpaca paper account…")
def live_equity() -> dict | None:
    """Refresh equity straight from Alpaca if paper keys are configured."""
    key, sec = _secret("ALPACA_API_KEY"), _secret("ALPACA_SECRET_KEY")
    if not (key and sec):
        return None
    import urllib.request
    req = urllib.request.Request(
        "https://paper-api.alpaca.markets/v2/account",
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
    try:
        a = json.load(urllib.request.urlopen(req, timeout=15))
        return {"equity": float(a["equity"]), "at": datetime.utcnow().strftime("%H:%M UTC")}
    except Exception:
        return None


SNAP = snapshot()
CFG = SNAP["config"]
usd = lambda n: ("-$" if n < 0 else "$") + f"{abs(n):,.0f}"


def card(col, label, value, note="", gold=False):
    col.markdown(f"""<div class="card"><div class="lbl">{label}</div>
      <div class="val{' gold' if gold else ''}">{value}</div>
      <div class="note">{note}</div></div>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ hero --
st.markdown('<div class="kick">Alpaca AI Trading Agents Hackathon · Sept 2026</div>',
            unsafe_allow_html=True)
st.markdown('<div class="hero">Every Trade Provable. <em>Every Loss Capped.</em></div>',
            unsafe_allow_html=True)
st.markdown(
    f"An autonomous 0DTE options agent on Alpaca paper. Maximum loss is capped by "
    f"construction, every order goes through the Alpaca **CLI**, and every decision — "
    f"including the refusals — is written down. &nbsp;·&nbsp; [Source]({REPO})")

tab_r, tab_g, tab_j, tab_h = st.tabs(
    ["  Live result  ", "  Try the agent  ", "  Decision journal  ", "  How it works  "])

# =========================================================== live result ==
with tab_r:
    live = live_equity()
    equity = live["equity"] if live else SNAP["equity"]
    pct = (equity / SNAP["start_equity"] - 1) * 100
    c = st.columns(4)
    card(c[0], f"Alpaca paper · {len(SNAP['sessions'])} sessions", f"{pct:+.1f}%",
         f"$100,000 → {usd(equity)}", gold=True)
    card(c[1], "Fills", f"{SNAP['entries']} / {SNAP['exits']}",
         "entries / exits, all via the Alpaca CLI")
    card(c[2], "Max loss per trade", "the premium",
         "single-leg long options — capped by the instrument")
    card(c[3], "Open at expiry", "never",
         f"hard flatten {CFG['risk'].get('eod_flatten', '15:50')} ET, every session")

    st.caption(f"Account `{SNAP['account']}` · "
               + (f"live, refreshed {live['at']}" if live
                  else f"snapshot taken {SNAP['generated_at']}")
               + " · paper trading only; hypothetical results, not investment advice.")

    st.markdown("#### Equity curve")
    points = [{"Session": lbl, "Equity": val, "n": i}
              for i, (lbl, val) in enumerate(zip(SNAP["labels"], SNAP["curve"]))]
    lo, hi = min(SNAP["curve"]), max(SNAP["curve"])
    pad = (hi - lo) * 0.12 or 1000
    try:
        import altair as alt
        st.altair_chart(
            alt.Chart(alt.Data(values=points)).mark_line(
                color=GOLD, strokeWidth=3, point=alt.OverlayMarkDef(
                    color=GOLD, size=70, filled=True)).encode(
                x=alt.X("Session:O", sort=alt.EncodingSortField("n"),
                        axis=alt.Axis(title=None, labelColor=INK2, domainColor=LINE,
                                      tickColor=LINE, labelAngle=0)),
                y=alt.Y("Equity:Q", scale=alt.Scale(domain=[lo - pad, hi + pad]),
                        axis=alt.Axis(title=None, format="$,.0f", labelColor=INK2,
                                      domainColor=LINE, tickColor=LINE,
                                      gridColor=LINE)),
                tooltip=[alt.Tooltip("Session:O"),
                         alt.Tooltip("Equity:Q", format="$,.0f")],
            ).properties(height=300).configure_view(strokeWidth=0)
            .configure(background="rgba(0,0,0,0)"), width="stretch")
    except Exception:                       # altair missing/changed — never blank
        st.line_chart({"Equity ($)": SNAP["curve"]}, color=GOLD, height=300)
    st.caption("Opening balance, then the closing equity after each session that "
               "actually filled orders. A day with no fills is not a session.")

    st.markdown("#### Every session")
    st.dataframe(
        [{"Session": s["day"], "Underlyings": ", ".join(s["symbols"]),
          "Entries": s["buys"], "Exits": s["sells"], "Contracts": s["contracts"],
          "Realised P&L": usd(s["pnl"])} for s in SNAP["sessions"]],
        width="stretch", hide_index=True)

# ========================================================== gate driver ===
with tab_g:
    st.markdown("#### Drive the real decision stack")
    st.markdown(
        "These controls are wired straight into `src/gate.py` and `src/risk.py` — "
        "the same objects the live agent constructs. Move anything and you get the "
        "verdict *and the exact rationale string* the agent would journal.")

    a, b = st.columns([1, 1.25], gap="large")
    with a:
        st.markdown("**The signal**")
        symbol = st.selectbox("Underlying", CFG.get("symbols", ["SPY", "QQQ", "IWM"]))
        score = st.slider("Score", 0.0, 1.0, 0.74, 0.01,
                          help=f"Gate threshold is {CFG['score']['min_score']:.2f}")
        direction = st.radio("Proposed direction", ["call", "put"], horizontal=True)
        band = st.radio("Half-OR noise band", ["outside", "inside", "unavailable"],
                        horizontal=True,
                        help="Is the break bigger than the symbol's own morning noise?")
        mode = st.radio("Entry mode", ["trend", "fade"], horizontal=True)
        above = st.slider("Mega-caps above their 20-day average", 0, 8, 6,
                          help="Leader breadth 'T6'. ≥5 of 8 ⇒ bullish regime.")

        st.markdown("**The account, right now**")
        clock = st.slider("Time (ET)", dtime(9, 30), dtime(16, 0), dtime(11, 15),
                          timedelta(minutes=5), format="HH:mm")
        openpos = st.slider("Open positions", 0, 5, 1)
        cost = st.slider("Premium this entry would cost ($)", 0, 1200,
                         int(CFG["risk"].get("max_premium_per_trade_usd", 200)) // 2, 10)
        holding = st.checkbox(f"Already holding an open {direction} on {symbol}")
        recent = st.checkbox(f"Entered a {direction} on {symbol} in the last "
                             f"{CFG.get('entry_rules', {}).get('dedup_minutes', 30):.0f} min")

    regime = {"state": "bull" if above >= CFG["regime"]["leader_min_above"] else "bear",
              "above": above, "counted": 8}
    scored = {"score": score, "would_have_direction": direction,
              "noise_band": None if band == "unavailable" else {"state": band},
              "entry_mode": mode}

    decision = RulesGate(CFG["score"], CFG.get("regime")).decide(scored, regime=regime)

    rm = RiskManager(CFG["risk"], CFG.get("entry_rules"))
    risk_ok, risk_why = None, None
    if decision.go:
        risk_ok, risk_why = rm.check_entry(RiskState(
            now=clock, open_positions=openpos, trades_today=openpos,
            premium_cost_usd=float(cost), symbol=symbol, direction=direction,
            already_holding=holding,
            mins_since_last_entry=5.0 if recent else None,
            directions_today=(direction,) if (holding or recent) else ()))

    traded = bool(decision.go and risk_ok)
    with b:
        if traded:
            st.markdown(f"""<div class="verdict go"><div class="big">▲ ENTER — buy the ATM {symbol} {direction}</div>
              <div class="why">{decision.rationale}</div></div>""", unsafe_allow_html=True)
        else:
            why = risk_why if decision.go else decision.rationale
            st.markdown(f"""<div class="verdict no"><div class="big">✕ NO TRADE</div>
              <div class="why">{why}</div></div>""", unsafe_allow_html=True)

        st.markdown("<br>**Where it stopped**", unsafe_allow_html=True)
        min_s = CFG["score"]["min_score"]
        steps = [
            (f"score {score:.2f} ≥ {min_s:.2f}", score >= min_s),
            ("outside its own half-OR noise band", band == "outside"),
            (f"leader regime is {regime['state']} ({above}/8) — "
             f"{direction} is the {'aligned' if decision.go or not (score >= min_s and band == 'outside') else 'opposed'} side",
             not (score >= min_s and band == "outside" and not decision.go)),
        ]
        for lbl, ok in steps:
            mark = "✓" if ok else "✕"
            cls = "pass" if ok else "fail"
            st.markdown(f'<div class="step"><span class="{cls}">{mark}</span> '
                        f'<b>gate</b> · {lbl}</div>', unsafe_allow_html=True)
        if decision.go:
            mark, cls = ("✓", "pass") if risk_ok else ("✕", "fail")
            st.markdown(f'<div class="step"><span class="{cls}">{mark}</span> '
                        f'<b>risk</b> · {risk_why}</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="step"><span class="skip">—</span> '
                        '<b>risk</b> · not reached; the gate already refused</div>',
                        unsafe_allow_html=True)

        st.markdown("<br>**The line it would write to the journal**", unsafe_allow_html=True)
        st.code(json.dumps({"type": "decision", "symbol": symbol, "score": round(score, 2),
                            "would_direction": direction, "entry_mode": mode,
                            "noise_band": scored["noise_band"], "regime": regime["state"],
                            "gate": {"go": decision.go, "source": decision.source,
                                     "rationale": decision.rationale}}), language="json")
        st.caption("With `decision_mode: llm`, a Claude bull/bear debate runs **after** this "
                   "gate and can only turn a go into a no-go — never the reverse. The "
                   "deterministic path is a strict subset of the AI path.")

    # ---- exit ladder ----------------------------------------------------
    st.divider()
    st.markdown("#### And once it is in — the exit ladder")
    ex = CFG["exits"]
    PREM = 2.50                       # a representative ATM 0DTE premium, in dollars
    e1, e2 = st.columns([1, 1.25], gap="large")
    with e1:
        st.caption(f"Say it bought the ATM contract at **${PREM:.2f}**.")
        pnl = st.slider("Position P&L (%)", -70.0, 90.0, 12.0, 1.0)
        held = st.slider("Minutes held", 0, 240, 20, 5)
        peak = st.slider("Best it ever showed (%)", 0.0, 120.0, max(pnl, 0.0), 1.0)
        scaled = st.checkbox("The +40% half has already sold (it is a runner now)")
        cut = st.checkbox("The −20% half has already sold")

    # Mirrors Executor._manage_scale_single's ordering exactly:
    # eod → stop ladder → (un-scaled) _exit_reason → (runner) trail.
    s1, s2 = float(ex.get("stop1_loss_pct", -20)), float(ex.get("stop2_loss_pct", -40))
    give = float(ex.get("runner_giveback_pct", 40))
    act = None
    if bool(ex.get("stop_ladder_enabled", False)) and pnl <= s2 + 1e-9:
        act = ("stop_loss_2", f"sell **everything left** — {pnl:.0f}% is at or past {s2:.0f}%")
    elif bool(ex.get("stop_ladder_enabled", False)) and not cut and pnl <= s1 + 1e-9:
        act = ("stop_loss_1", f"sell **half** — {pnl:.0f}% is at or past {s1:.0f}%. The "
                              "remainder is *not* marked a runner, so it keeps its time "
                              "stop and profit target.")
    elif not scaled:
        probe = Position(symbol=symbol, direction=direction, contract="—", strike=0.0,
                         expiration="—", qty=2, entry_ask=PREM, entry_mid=PREM,
                         entry_time=datetime(2026, 9, 3, 10, 0), cost_usd=PREM * 200)
        reason = Executor._exit_reason(  # the agent's own function, called directly
            type("‗", (), {"exits_cfg": ex})(), probe, PREM * (1 + pnl / 100.0),
            probe.entry_time + timedelta(minutes=held), False)
        if reason == "profit_target":
            act = ("profit_target", f"sell **half** at +{pnl:.0f}%, and trail the runner "
                                    f"by {give:.0f}% of its peak gain")
        elif reason:
            act = (reason, {"time_stop": f"**close it** — {held} minutes held and still not "
                                         f"green ({pnl:.0f}%)",
                            "premium_stop": f"**close it** at {pnl:.0f}%"}.get(reason, reason))
    else:
        floor = peak * (1 - give / 100.0)
        if pnl <= floor and peak > 0:
            act = ("runner_trail", f"**close the runner** — it peaked at +{peak:.0f}% and "
                                   f"has given back to {pnl:.0f}%, at or below the "
                                   f"{floor:.0f}% floor")
    with e2:
        if act:
            st.markdown(f"""<div class="verdict no" style="border-color:{GOLD};
              background:rgba(240,180,41,.09)"><div class="big" style="color:{GOLD}">
              → {act[0]}</div><div class="why">{md(act[1])}</div></div>""",
                        unsafe_allow_html=True)
        else:
            st.markdown("""<div class="verdict" style="border-color:#232A38">
              <div class="big" style="color:#A1A8B5">→ hold</div>
              <div class="why">No rung is triggered. It is checked again on the next
              scan, and flattened unconditionally at the close.</div></div>""",
                        unsafe_allow_html=True)
        st.markdown(
            f"""<div style="margin-top:1rem">
            <div class="step"><b>+{ex.get('profit_target_pct', 40):.0f}%</b> · sell half, trail the runner by {give:.0f}% of peak gain</div>
            <div class="step"><b>−{abs(s1):.0f}%</b> · sell half</div>
            <div class="step"><b>−{abs(s2):.0f}%</b> · sell the rest</div>
            <div class="step"><b>{ex.get('time_stop_minutes', 30):.0f} min</b> · close it if it is not green</div>
            <div class="step"><b>{CFG['risk'].get('eod_flatten', '15:50')} ET</b> · flatten everything, no exceptions</div>
            </div>""", unsafe_allow_html=True)

# ======================================================= decision journal ==
with tab_j:
    st.markdown("#### The record the agent writes as it goes")
    st.markdown(
        "One JSONL line per scan: the score, every signal, the band state, the gate "
        "verdict, the risk verdict, and the order — or the absence of one. **Most "
        "agents show you the trades they took. This shows the ones it refused, and "
        "exactly why.**")

    recs = SNAP.get("journal", [])
    dec = [r for r in recs if r.get("type") == "decision"]
    refused = [r for r in dec if not (r.get("gate") or {}).get("go")]
    c = st.columns(4)
    card(c[0], "Records", f"{len(recs)}", "every scan, appended")
    card(c[1], "Decisions", f"{len(dec)}", "one per symbol per scan")
    card(c[2], "Refused", f"{len(refused)}", "and each one says why", gold=True)
    card(c[3], "Sessions logged", f"{len({r.get('ts', '')[:10] for r in recs})}",
         "one file per day")

    kinds = sorted({r.get("type", "?") for r in recs})
    pick = st.multiselect("Record type", kinds, default=kinds)
    only_no = st.checkbox("Only the refusals", value=False)
    shown = [r for r in recs if r.get("type") in pick
             and not (only_no and (r.get("type") != "decision"
                                   or (r.get("gate") or {}).get("go")))]

    for r in shown[::-1][:60]:
        g = r.get("gate") or {}
        head = (f"{r.get('ts_et', '')[11:19]}  ·  {r.get('type')}"
                + (f"  ·  {r['symbol']}" if r.get("symbol") else "")
                + (f"  ·  {'GO' if g.get('go') else 'no-go'} — {g.get('rationale')}"
                   if r.get("type") == "decision" else ""))
        with st.expander(head):
            st.code(json.dumps(r, indent=2), language="json")
    if not shown:
        st.info("Nothing matches that filter.")
    st.caption("Journal shipped in the repo at `logs/` and embedded in "
               "`assets/snapshot.json`. It contains no keys, no account identifiers "
               "and no personal data.")

# ============================================================ how it works ==
with tab_h:
    st.markdown("#### One loop, no human in it")
    st.code("""every scan:  manage open positions (exit first)
             for each symbol:
               Alpaca 5m bars ─▶ score (VWAP, opening range, RSI, EMA, rel-vol)
                              ─▶ noise band (is the break real?)
                              ─▶ GATE   (rules_only  |  llm veto)
                              ─▶ RISK   (defined-risk caps, session window)
                              ─▶ EXECUTE (ATM long via the Alpaca CLI,
                                          polling for the real fill)
             exits: +40% scale half, trail the runner / −20% half, −40% rest
                    / 30-min time stop / 15:50 EOD flatten""", language="text")

    l, r = st.columns(2, gap="large")
    with l:
        st.markdown("#### Risk, in the order it is enforced")
        for lbl, txt in [
            ("structural", "single-leg long options only — the **maximum loss is the "
                           "premium paid**, set by the instrument, not by code that has "
                           "to remember"),
            ("one lot", "never a second open lot on a symbol+direction already held"),
            ("one direction", "no simultaneous call *and* put on the same underlying"),
            ("dedup", f"{CFG.get('entry_rules', {}).get('dedup_minutes', 30):.0f} min, "
                      "entry-anchored — a score that stays hot cannot stack the name"),
            ("sizing", "trimmed to the account's **real** options buying power, so an "
                       "expensive contract sizes *down* instead of rejecting"),
            ("session", f"no entry after {CFG['risk'].get('no_entry_after', '15:00')} ET; "
                        f"hard flatten {CFG['risk'].get('eod_flatten', '15:50')} — a 0DTE "
                        "never reaches expiry"),
            ("orphan sweep", "any option position the agent did not open is flattened at "
                             "startup and at the close"),
        ]:
            st.markdown(f"<div class='step'><b>{lbl}</b> · {md(txt)}</div>",
                        unsafe_allow_html=True)
    with r:
        st.markdown("#### Alpaca, end to end")
        for lbl, txt in [
            ("CLI execution", "every order goes through `alpaca order submit` and is "
                              "polled to a terminal state — so P&L is the **real broker "
                              "fill**, not a quote"),
            ("paper proof", "before placing anything it verifies `alpaca doctor` resolves "
                            "`paper-api.alpaca.markets`, and refuses otherwise — the env "
                            "var alone is not proof"),
            ("no duplicates", "an ambiguous submit is reconciled by `--client-order-id` "
                              "before it will ever resubmit"),
            ("market data", "5-minute bars, contract discovery and ATM quotes from the "
                            "Alpaca Market Data API"),
            ("unattended", "runs as an isolated Docker container under a supervisor that "
                           "starts one session each weekday; keys injected at runtime, "
                           "never baked into the image"),
            ("tested", "174 network-free tests over the scorer, the gates, the sizing "
                       "maths and the execution path"),
        ]:
            st.markdown(f"<div class='step'><b>{lbl}</b> · {md(txt)}</div>",
                        unsafe_allow_html=True)

    st.divider()
    st.markdown(f"Full write-up: **[docs/ONE_PAGER.md]({REPO}/blob/main/docs/ONE_PAGER.md)** · "
                f"deck: **[assets/deck.pdf]({REPO}/blob/main/assets/deck.pdf)** · "
                f"the year of OPRA validation behind these controls: "
                f"**[docs/research.md]({REPO}/blob/main/docs/research.md)**")
    st.caption("Paper trading only. Hypothetical results; not investment advice.")
