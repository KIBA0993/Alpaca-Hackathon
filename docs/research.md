# How a year of OPRA data shaped this agent

Before writing the agent we ran its entry logic across a year of **real Alpaca OPRA
option bars** — SPY, QQQ and IWM, roughly 2025-09 to 2026-08, **264 sessions and 4,544
alerts** — and used the results to decide what belonged in the code and what didn't.
This page is what that testing changed. Every number here comes from the audited
harness, whose headline totals are asserted by a regression gate so they cannot drift.

## How we tested

Three rules governed every result, and they are the reason the list of things we kept
is short:

- **Sessions get an equal vote.** Intraday option data is dominated by a handful of
  busy days. Pooling raw signals lets those days speak for the year, so every
  statistic is clustered by session before it counts.
- **Every candidate is measured against a matched null.** A rule that trades less is
  compared to cutting the *same number* of trades at random. Anything that only beats
  "do nothing" is not an edge, it is a smaller sample.
- **A second, adversarial pass.** Each result was re-derived independently before it
  was allowed to shape the design. The figures below are the ones that survived that
  pass, not the first-pass ones.

## What the data actually supported

| we tested | verdict |
|---|---|
| entry filters — relative volume, first-scan location, opening-range timing | did not survive session clustering; **not shipped** |
| the noise-band width itself | no width beat its own matched null; the band ships at half the opening range, unchanged |
| defined-risk spread selling | worse than the long structure after costs; **not shipped** |
| single-leg long structure | **kept** — maximum loss is fixed by the instrument |
| a hard session window and end-of-day flatten | **kept** — the only controls that held across every cut |

The recurring trap, and the reason the null matters: *any rule that cuts position
count flatters a book.* Several candidates looked strong until they were compared
against a random cut of the same size, at which point the apparent gain disappeared.

## What that means for the design

The testing did not hand us a filter to lean on. It handed us three design
constraints, and those are what the agent is built around:

- **Risk is structural, not statistical.** Single-leg long options only, so the
  maximum loss is set by the instrument rather than by code that has to remember. The
  agent cannot blow up regardless of what any signal does.
- **The AI is scoped to removal.** The Claude layer can turn a *go* into a *no-go* and
  never the reverse, which makes the deterministic path a strict subset of the AI path
  and lets the two be compared directly rather than argued about.
- **Everything is written down.** Each scan appends one JSONL record carrying the
  score, every signal, the band state, the gate verdict, the risk verdict and the
  order — or the absence of one. A decision you cannot reconstruct is a decision you
  cannot improve.

## Reproducing it

The harness lives outside this repo (it carries a large OPRA bar cache), but its
conclusions are the ones encoded here: `src/score.py` is the scorer it tested,
`src/risk.py` and `src/execution.py` are the controls that survived, and
`config.json` is the exact configuration the submitted agent ran.

*Paper trading only. Hypothetical results; not investment advice.*
