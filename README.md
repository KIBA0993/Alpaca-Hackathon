# Every Trade Provable. Every Loss Capped.

> ### Required one-page write-up — AI logic · risk gates · Alpaca infrastructure
> It ships in all three accepted forms:
> **[`docs/ONE_PAGER.md`](docs/ONE_PAGER.md)** (repo page) ·
> **[`docs/ONE_PAGER.pdf`](docs/ONE_PAGER.pdf)** (one printed page) ·
> **slide 9 of [`assets/deck.pptx`](assets/deck.pptx)** (presentation).
>
> **Try it live — [every-trade-provable.streamlit.app](https://every-trade-provable.streamlit.app)** ·
> the result, the *real* gate you can drive yourself, and the decision journal.
>
> Alpaca paper account `PA38HG4D9653`, opened fresh for this event at $100,000.
> The scored sessions ran `decision_mode: rules_only` — the deterministic stack — with the
> Claude veto (`src/llm.py`, `src/gate.py`) shipped tested and runnable via
> `--decision-mode llm`.

An autonomous intraday options agent for the Alpaca AI Trading Agents Hackathon. It
trades **single-leg long 0DTE options** on SPY / QQQ / IWM through Alpaca, with risk
**defined by construction** — a long option can only lose the premium paid.

From a fresh $100,000 paper account it finished at **$179,087 (+79.1%)** across three
trading sessions: 34 entries, 43 exits, every order placed through the Alpaca **CLI**
and booked at the real broker fill rather than a quote.

What sets it apart is that every decision is checkable. Each control in the agent
earned its place against a year of real Alpaca OPRA option bars — 264 sessions and
4,544 alerts — and every scan writes a full reasoning record, including the trades it
**refused** to take.

## The decision gate (one flag apart)

The gate is the only place the two flavours differ. Flip one flag in `config.json`:

| `decision_mode` | what it is | gate |
|---|---|---|
| `rules_only` | the deterministic rule stack (score + half-OR band + leader regime) | score ≥ 0.70 **and** price outside its own half-OR noise band **and** direction agrees with the leader-breadth regime **and** one direction per underlying per session **and** no re-entry within 15 min of a `time_stop` exit on that symbol |
| `llm` | the core **plus** an AI bull/bear debate + regime read | rules gate, then an LLM **veto** |

The LLM can only turn a *go* into a *no-go* — it can never create a trade the
rules rejected. So `rules_only` is a strict subset of `llm`: the two can be run side by side
and compared against each other.

## How it works

```
every scan:  manage open positions (exit first)
             for each symbol:
               Alpaca 5m bars ─▶ score (VWAP, opening range, RSI, EMA, rel-vol)
                              ─▶ noise band (is the break real?)
                              ─▶ GATE   (rules_only  |  llm veto)
                              ─▶ RISK   (defined-risk caps, session window)
                              ─▶ EXECUTE (dry_run logs, or paper buys ATM long
                                          via the Alpaca CLI, polling for the fill)
             exits: +40% target (scale out half, trail the runner) /
                    −20% sell half, −40% the rest / 30-min time stop /
                    15:50 EOD flatten
```

**Sizing is adaptive and exits scale out.** The agent targets `contracts_per_trade`
but trims the size to fit both the per-trade premium budget and the remaining
buying-power room (capped by the account's real options BP), so an expensive
contract sizes *down* instead of rejecting. At the +40% target it sells **half**
and trails the **runner** by giving back 40% of its peak gain (`runner_trail`).
On the downside a stop ladder sells **half at −20%** and the **rest at −40%**; the
half-cut deliberately does not mark the remainder a runner, so it keeps its time
stop and profit target. Time-stop / EOD sell all remaining. (`premium_stop_pct`
is only reachable with `stop_ladder_enabled: false` — the −40% rung catches
anything deeper first.) Partial fills are handled on
both sides, entries use a marketable limit, and any un-tracked broker option
position is flattened at startup and end-of-day. Set `contracts_per_trade: 1` and
`scale_out_at_target: false` for the simple 1-lot full-exit path.

**Orders go through the Alpaca CLI**, not the SDK — that satisfies the hackathon's
"must use Alpaca's MCP server or CLI" rule, and it means the agent books the
*real* broker fill price, not a quote. Market *data* (5m bars, contract discovery,
quotes) still comes from the Alpaca SDK. A paper order only becomes a position
when it actually **fills**; a failed close leaves the position open to retry, so
the agent never loses track of a live position.

Two safety belts adopted from Alpaca's official CLI skill: before placing
anything, the agent verifies `alpaca doctor` resolves the **paper** endpoint
(`https://paper-api.alpaca.markets`) — the env var alone isn't proof, since a
profile's `live_trade` can route live — and refuses otherwise. On an ambiguous
submit failure it reconciles by `--client-order-id` (`alpaca order
get-by-client-id`) before ever resubmitting, so a stalled order is never
duplicated or orphaned.

Every scan writes one JSONL record with the **full reasoning** — score, each
signal, band state, gate verdict, risk verdict, and any order — to
`logs/decisions-YYYY-MM-DD.jsonl` — **committed to this repo**, so any decision can be
checked against the record. The audit trail is the product.

## Modules

| file | role |
|---|---|
| `src/score.py` | the scorer, reproduced faithfully from the live arm (pure, unit-tested) |
| `src/regime.py` | leader-breadth regime ("T6"): ≥5 of 8 mega-caps above their own 20-DMA as of the last **completed** session ⇒ bullish; only the opposed side is ever refused |
| `src/marketdata.py` | Alpaca 5m bars, rvol/band baselines, ATM contract + quote |
| `src/gate.py` | `rules_only` vs `llm` decision gate (the swappable core) |
| `src/llm.py` | the optional bull/bear + regime advisor (veto-only) |
| `src/risk.py` | defined-risk caps + session windows (pure, unit-tested) |
| `src/execution.py` | ATM long entry + exit management (dry_run / paper) |
| `src/broker_cli.py` | order placement through the Alpaca **CLI** (submit + poll for fill) |
| `src/agent.py` | the scan→gate→risk→execute→manage loop |
| `src/journal.py` | append-only JSONL decision log |
| `streamlit_app.py` | the hosted demo — drives the real gate, risk and exit code |
| `docs/research.md` | the year of OPRA validation behind these controls |

## The hosted demo

**[every-trade-provable.streamlit.app](https://every-trade-provable.streamlit.app)** — three
things a repo cannot show you:

| tab | what it does |
|---|---|
| **Live result** | the equity curve, every session's realised P&L, and the fill counts, straight from the paper account |
| **Try the agent** | sliders wired into `src/gate.py` and `src/risk.py` — **the agent's own objects**, not a mock-up. Move the score, the noise band or the leader breadth and you get the verdict *and the exact rationale string it would journal*. A second panel does the same for the exit ladder, calling `Executor._exit_reason` directly |
| **Decision journal** | the real JSONL records, filterable down to just the **refusals** |

It needs no API keys: the account figures come from `assets/snapshot.json`, frozen from the
live account by `assets/make_snapshot.py`. Give it Alpaca paper keys (environment or
`.streamlit/secrets.toml`) and the equity refreshes live instead.

```bash
pip install -r requirements.txt
python3 assets/make_snapshot.py     # refresh the frozen account figures (needs .env)
streamlit run streamlit_app.py      # http://localhost:8501
```

<details><summary>Deploying it (Streamlit Community Cloud, free)</summary>

1. <https://share.streamlit.io> → **Create app** → *Deploy a public app from GitHub*
2. Repository `KIBA0993/Alpaca-Hackathon`, branch `main`, main file `streamlit_app.py`
3. **Advanced settings → Python version `3.11`** (the stack is pinned to it)
4. Custom subdomain `every-trade-provable`, then **Deploy**

Nothing secret is needed — it runs off the committed snapshot. To make the equity refresh
live instead, add `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` under *Settings → Secrets*; they
are only ever used for a read-only `GET /v2/account`.
</details>

## Run it

Prereqs for placing paper orders: install the **Alpaca CLI** (`alpaca`, on your
`PATH`) and enable **Options Level 2** on the paper account (long single-leg
calls/puts need it, or the buy rejects).

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in your Alpaca PAPER keys (the CLI reads these too)
python smoke_trade.py                               # preflight: auth, options level, ATM quote (no orders)
python smoke_trade.py --live --yes                  # market open: one real paper round-trip via the CLI
python -m src.agent --once                          # one scan, dry-run (no orders)
python -m src.agent --once --decision-mode llm      # add the AI veto (needs ANTHROPIC_API_KEY)
python -m src.agent --loop --mode paper             # trade the session on the paper account
```

`--mode dry_run` (the default) places **no orders** — it fetches live data,
scores, decides, and logs the intended trade. `--mode paper` places real orders
on your Alpaca **paper** account through the CLI. There is no live-money path in
this repo. `smoke_trade.py` is the standalone "can I actually place + sell an
option?" check — preflight-only until you pass `--live --yes`, and it refuses to
place anything while the market is closed.

```bash
pytest -q                     # 174 network-free tests: scorer, gate, risk, CLI execution, sizing + runner
```

## Implementation notes

- **The AI can only remove risk.** The veto turns a *go* into a *no-go* and never
  the reverse, so the deterministic path is a strict subset of the AI path and the
  two can be run side by side and compared.
- **One simplification vs live:** the call/put-ratio scoring atom (weight 0.10,
  the smallest) is disabled here because it needs a full option-chain volume pull;
  it cannot lift a signal over the 0.70 gate on its own.
- **Fills cross the spread.** In **paper**, cash P&L is the *real* broker fills
  (buy fill vs sell fill); the % you see is mid-based, so the two can differ by
  the spread — both are reported, neither is hidden. In **dry_run** (no broker),
  it falls back to modeled ask-in / bid-out. Triggers always read the mid.
