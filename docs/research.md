# Why this agent does not claim an edge

This is the honest core of the submission. Before building anything, we ran the
strategy through a year of real market data and tried hard to *break* our own
assumptions. Here is what we found, and why it shaped the agent into a
defined-risk, transparency-first design rather than a P&L story.

All tests are on **real Alpaca OPRA option bars and equity bars** — SPY, QQQ, IWM
— over roughly **2025-09 to 2026-08** (~248 sessions). The entry selection was
reconstructed from the live scorer's own logic. Every test was reviewed by a
second, adversarial pass; the numbers below are the ones that *survived* review,
not the first-pass ones.

## 1. The full-year P&L is negative — and it's an estimate, not a live record

Simulating the long-option strategy over the year (1.5%/leg spread crossed, the
live 40% / −65% / 30-min exits) books a **loss**: about **−$8/trade**, ~40% win
rate, both calls and puts underwater (puts worse, having fought the year's
up-drift). This is a *simulation* on real bars, not a live account statement.

## 2. There is no session-robust directional edge

We removed the exit rule entirely and asked the cleanest possible question: does
a score ≥ 0.70 signal predict the underlying's direction over the next
15 / 30 / 60 minutes? Benchmarked against the *honest* alternative — "just buy a
call and ride the drift" on the same bars:

| horizon | signal hit-rate | just-buy-calls | honest edge |
|---|--:|--:|--:|
| +15 min | 51.8% | 51.2% | **+0.6pp** |
| +30 min | 52.4% | 51.7% | **+0.7pp** |
| +60 min | 52.1% | 52.6% | **−0.5pp** |

And when each **session** gets an equal vote (instead of high-activity days
dominating), the hit-rate is **49.4% / 49.2% / 47.8%** — below a coin flip, and
*worse* with horizon. The apparent "52%" was a size-weighting artifact of a
handful of busy sessions; the true sample is ~248 correlated session-days, not
the ~10,000 raw signals.

**The "call skill" is just the up-market.** Calls drift to ~55% with horizon,
puts fall to ~49% — strip the year's rising tide and the direction call adds
essentially nothing.

## 3. The move is too small to pay the premium anyway

Median 30-minute underlying move after a signal is **~11 bp**. Crossing the
option spread alone needs **~20 bp** *before* any time decay. Only ~15% of
favorable moves clear that bar. Even a perfect direction call can't pay for the
option at this move size — which is exactly what the −$8/trade P&L confirms.

## 4. The pieces we thought were edges weren't

Across the research we tested — and retired — a long list of candidate edges:
entry filters (relative volume, first-scan location, opening-range timing),
the noise-band width itself, and defined-risk spread-selling. Each looked
promising on a naive cut and each **failed** the honest test: session-clustering,
adversarial refutation, or a random-cut null of equal size. The recurring trap:
*any rule that cuts position count flatters a negative-expectancy book.*

## What we concluded — and how it shaped this agent

The entry signal carries no usable directional information we can trade on. So
we did **not** build the agent around a profit claim. Instead:

- **Defined risk by construction.** Single-leg long options: max loss = premium.
  No path to a blow-up, which is the point when there is no edge to lean on.
- **The AI is a risk manager, not an alpha source.** The LLM gate can only
  *veto* (sit out a bad regime). We never let it manufacture conviction we
  couldn't measure — because we measured, and it wasn't there.
- **Radical transparency.** Every decision is journaled with its full reasoning.
  The deliverable is an auditable agent you can *trust to behave*, not a curve you
  have to believe.

This mirrors the maturing consensus in the AI-trading literature: reporting
discipline, honest held-out evaluation, and realistic costs matter more than a
novel architecture with an unfalsifiable backtest. We would rather submit an
agent that is honest about having no edge than one that hides a negative one.
