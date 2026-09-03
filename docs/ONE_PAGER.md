# The Agent That Failed Its Own Backtest

**Alpaca AI Trading Agents Hackathon — one-page write-up**
Paper account `PA38HG4D9653` · SPY / QQQ / IWM single-leg long 0DTE · paper only, no live path in the repo.

Most submissions open with an edge claim. This one opens with a refutation. We backtested our entry
signal on a year of **real Alpaca OPRA option bars** (~248 sessions, 2025-09 → 2026-08) and it
**failed**: ~−$8/trade, ~40% win rate, and — once each session gets an equal vote instead of letting a
few busy days dominate — a directional hit-rate of **49.4% / 49.2% / 47.8%** at +15/30/60 min, *below a
coin flip* and worse than "just buy a call." Median 30-minute move after a signal is ~11 bp; crossing
the option spread costs ~20 bp before decay. So we stopped trying to sell a signal and built the thing
that survives not having one: **defined risk by construction, code-enforced gates, and a decision trail
you can audit line by line.** `docs/research.md` is the full autopsy, including the filters we retired.

## 1 · AI logic

The agent is a `scan → score → gate → risk → execute → manage` loop. A **pure, unit-tested scorer**
(`src/score.py`, ported faithfully from our live desk) reduces Alpaca 5-minute bars to one number from
VWAP position, opening-range break, RSI, EMA stack and relative volume, plus a proposed direction. Two
independent regime reads sit above it: a **half-opening-range noise band** (is this break bigger than
the symbol's own morning noise?) and **T6 leader breadth** — ≥5 of 8 mega-caps above their own 20-DMA
as of the last *completed* session. T6 only ever removes the opposed side; it never flips a direction
and never creates a trade.

The LLM layer (`src/llm.py`, Claude Sonnet) is deliberately **veto-only**. It receives the scored facts,
argues bull and bear, reads the regime, and returns strict JSON — and `LLMGate` can only turn a *go*
into a *no-go*. It can never resurrect a trade the rules rejected, so `rules_only` is a **strict
mathematical floor** under `llm` and the two are one config flag apart. Missing key or bad JSON degrades
to `rules_only` and says so in the log rather than failing open. This scoping is a direct consequence of
the research: we could not measure the model adding P&L, so we gave it the only job we could defend —
sitting out — instead of letting it manufacture conviction. **The scored run was `rules_only`;** the
LLM gate ships tested and runnable via `--decision-mode llm`.

## 2 · Risk gates

Risk is structural first, procedural second. **Single-leg long options only: maximum loss is the premium
paid, enforced by the instrument, not by code that has to remember.** On top of that, `src/risk.py` and
`src/execution.py` enforce, in order:

| gate | rule |
|---|---|
| one-lot guard | never a second open lot on a `(symbol, direction)` already held |
| one direction / underlying | no simultaneous call *and* put on the same symbol |
| entry dedup | 30 min, **entry-anchored** — a persistent score can't stack the same name |
| scoped cooldown | 15 min re-entry block, armed only by a `time_stop` close |
| per-scan throttle | ≤2 new entries per scan pass |
| adaptive sizing | 300-lot target trimmed to fit the account's **real** options buying power — an expensive contract sizes *down* instead of rejecting |
| aggregate premium | ≤ `min($95k, live options BP)` at risk across all open lots |
| session window | no entry after 15:00 ET; **hard flatten 15:50 ET** — never carry a 0DTE into expiry |
| orphan sweep | any broker option position the agent did not open is flattened at startup and EOD |

Exits are a ladder, not a coin flip: **+40% target scales out half** and trails the runner by 40% of its
peak *gain*; the downside sells **half at −20%, the rest at −40%**, with −65% kept as a gap backstop;
30-minute time stop; EOD flatten. **175 network-free tests** cover the scorer, gate, risk rules, sizing,
runner trail and CLI execution.

## 3 · Alpaca infrastructure

**Orders go through the Alpaca CLI, not the SDK** (`src/broker_cli.py`): `alpaca order submit`, then
poll to a terminal state. That means the agent books the *real broker fill*, not a quote — cash P&L is
buy-fill vs sell-fill. Two safety belts from Alpaca's own CLI skill: before placing anything the agent
verifies **`alpaca doctor` resolves `paper-api.alpaca.markets`** and refuses otherwise (the env var
alone isn't proof — a profile's `live_trade` can route live), and on an ambiguous submit failure it
reconciles by `--client-order-id` via `alpaca order get-by-client-id` before *ever* resubmitting, so a
stalled order is never duplicated or orphaned. A paper order becomes a position only when it actually
**fills**; a failed close leaves the position open to retry, so the agent never loses track of a live
lot. Partial fills are handled on both sides; entries are marketable limits (ask + 2%) to bound
slippage at size.

Market **data** — 5-minute bars, contract discovery, ATM quotes, the 20-DMA leader basket — comes from
the Alpaca Market Data API via the Python SDK. The agent runs unattended as an **isolated Docker
container on a Synology NAS**, `TZ=America/New_York`, under a supervisor that launches one session per
weekday at 09:20 ET and lets the agent self-exit at its own 15:50 flatten. Keys are injected at runtime,
never baked into the image. Every scan appends one JSONL record — score, each signal, band state, gate
verdict, risk verdict, order — to `logs/decisions-YYYY-MM-DD.jsonl`.

## Result, stated honestly

From a fresh $100,000 paper account opened for this hackathon, the agent traded **4 sessions** and
**doubled it — +101.9%**, across 32 entries and 38 exits on SPY/QQQ/IWM.

We are not going to tell you that is edge. Our own year of data says this signal has none, one session
supplies most of the gain, and the same distribution handed us **−10.7% on day one**. Long 0DTE options
at size are a high-variance instrument, and four sessions is not a sample — it is an anecdote with a
good outcome. What is reproducible is the part underneath: max loss fixed by the instrument, an AI that
is only allowed to say no, gates that hold when the P&L is ugly, and a journal that records every trade
the agent **refused**. That is the deliverable. The equity curve is just what happened this week.

*Paper trading only. Hypothetical results; not investment advice.*
