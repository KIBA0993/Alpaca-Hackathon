# Every Trade Provable. Every Loss Capped.

> ### Required one-page write-up — AI logic · risk gates · Alpaca infrastructure
> It ships in all three accepted forms:
> **[`docs/ONE_PAGER.md`](docs/ONE_PAGER.md)** (repo page) ·
> **[`docs/ONE_PAGER.pdf`](docs/ONE_PAGER.pdf)** (one printed page) ·
> **slide 9 of [`assets/deck.pptx`](assets/deck.pptx)** (presentation).
>
> Alpaca paper account `PA38HG4D9653`, opened fresh for this event at $100,000.
> The scored sessions ran `decision_mode: rules_only` — the deterministic stack — with the
> Claude veto (`src/llm.py`, `src/gate.py`) shipped tested and runnable via
> `--decision-mode llm`.

An intraday options agent for the Alpaca AI Trading Agents Hackathon. It trades
**single-leg long 0DTE options** on SPY / QQQ / IWM through Alpaca, with risk
**defined by construction** (a long option can only lose the premium paid).

What makes this submission different is not a profit claim. It is the opposite:
we spent the research phase trying to *disprove* our own edge, and this repo is
built on what survived. **A full year of backtesting on the real OPRA tape showed
the entry signal has no usable directional edge** — so we do not pretend it does.
The agent is honest about what it is: disciplined, defined-risk execution with a
transparent, auditable decision trail, and an AI layer whose job is *risk-off
judgement*, not fake alpha.

## The two agents (one flag apart)

The decision gate is the only place the two flavours differ. Flip one flag in
`config.json`:

| `decision_mode` | what it is | gate |
|---|---|---|
| `rules_only` | the rule stack (live "arm C" entry rules + half-OR band + leader regime) | score ≥ 0.70 **and** price outside its own half-OR noise band **and** direction agrees with the leader-breadth regime **and** one direction per underlying per session **and** no re-entry within 15 min of a `time_stop` exit on that symbol |
| `llm` | the core **plus** an AI bull/bear debate + regime read | rules gate, then an LLM **veto** |

The LLM can only turn a *go* into a *no-go* — it can never create a trade the
rules rejected. So `rules_only` is a strict floor under `llm`, and you can run
both side by side and compare them honestly. That live A/B **is** the demo.

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
                    −65% premium stop / 30-min time stop / EOD flatten
```

**Sizing is adaptive and exits scale out.** The agent targets `contracts_per_trade`
but trims the size to fit both the per-trade premium budget and the remaining
buying-power room (capped by the account's real options BP), so an expensive
contract sizes *down* instead of rejecting. At the +40% target it sells **half**
and trails the **runner** by giving back 40% of its peak gain (`runner_trail`);
premium-stop / time-stop / EOD sell all remaining. Partial fills are handled on
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
`logs/decisions-YYYY-MM-DD.jsonl`. The audit trail is the product.

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
| `docs/research.md` | **why we don't claim an edge** — the honest research |

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
pytest -q                     # 175 network-free tests: scorer, gate, risk, CLI execution, sizing + runner
```

## Honesty notes (read these)

- **No edge claim.** The entry signal was tested for a year and does not beat
  "just buy a call" by a session-robust margin. See `docs/research.md`. We ship
  it for *defined-risk discipline and transparency*, not expected profit.
- **The AI is a risk manager, not an oracle.** It only vetoes. We have not shown
  it adds P&L, and we say so — which is exactly why it is scoped to remove trades,
  not manufacture them.
- **One simplification vs live:** the call/put-ratio scoring atom (weight 0.10,
  the smallest) is disabled here because it needs a full option-chain volume pull;
  it cannot lift a signal over the 0.70 gate on its own.
- **Fills cross the spread.** In **paper**, cash P&L is the *real* broker fills
  (buy fill vs sell fill); the % you see is mid-based, so the two can differ by
  the spread — both are reported, neither is hidden. In **dry_run** (no broker),
  it falls back to modeled ask-in / bid-out. Triggers always read the mid.
