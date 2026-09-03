# Video presentation — script, shot list, recording plan

**Target: 3:00.** Judges watch a lot of these; the hook has to land in the first 15 seconds.
Everyone else opens with "our agent finds alpha." You open by saying yours doesn't. Lead with that.

Deck: `assets/deck.pptx` (8 slides) · Cover: `assets/cover.png`

---

## Before you record

```bash
node assets/make_deck.js && python3 assets/make_cover.py   # refresh to the final numbers
```

Then set up three windows you'll cut between:

| # | Window | What's on it |
|---|---|---|
| A | Deck, presenter mode, full screen | the 8 slides |
| B | Terminal, large font (18pt+), dark theme | the live demo commands below |
| C | Browser on the Alpaca paper dashboard | account `PA38HG4D9653` — positions + P&L |

**Mute notifications. Hide your bookmarks bar. Check no API key is visible anywhere in
window B or C before you hit record** — `alpaca doctor` prints a key prefix, so crop or
scroll past it.

---

## Demo commands (rehearse once, then record)

```bash
alpaca doctor                                    # shows the PAPER endpoint the agent verifies
python -m src.agent --once                       # one live scan, dry-run: score → gate → risk, no orders
tail -n 1 logs/decisions-$(date +%F).jsonl | python3 -m json.tool   # the full reasoning for one decision
pytest -q                                        # 175 passed
```

The money shot is the **third** one — a judge seeing a complete decision record, including a
*refusal*, is the single most persuasive frame in the video. If today's log has a
`"go": false` record, use that one, not a fill.

---

## Script

Read it conversationally — don't recite. Bracketed lines are stage directions.

### 0:00 — 0:18 · Hook  *[Slide 1]*

> Every agent in this hackathon is going to tell you it found an edge.
>
> I'm going to tell you mine didn't.
>
> We tested our entry signal on a full year of real Alpaca OPRA option bars — about 248
> sessions — and it failed. Minus eight dollars a trade. A directional hit rate below a
> coin flip. So this submission isn't a strategy pitch. It's what you build when you're
> honest about not having one.

### 0:18 — 0:48 · The research  *[Slide 2]*

> Here's what we actually found. We stripped the exit rules out and asked the cleanest
> question we could: does a score above our threshold predict direction?
>
> Size-weighted, it looked like fifty-two percent. But once every *session* gets an equal
> vote instead of letting a handful of busy days dominate — 49.4, 49.2, 47.8 percent.
> Below a coin flip, and getting *worse* with horizon.
>
> And the move is too small to pay for the option anyway. Median thirty-minute move after
> a signal: eleven basis points. Crossing the spread costs twenty. Even a perfect direction
> call doesn't clear the cost.
>
> Every filter we thought was an edge failed the same way. The full autopsy is in the repo.

### 0:48 — 1:08 · The design  *[Slide 3]*

> So we stopped trying to sell a signal, and built the three things that survive not
> having one.
>
> Fix the loss structurally. Scope the AI to removal only. And journal everything —
> including the trades it refuses.

### 1:08 — 1:33 · Risk gates  *[Slide 4]*

> Risk is structural before it's procedural. Single-leg long options only — maximum loss
> is the premium paid, and that's enforced by the *instrument*, not by code that has to
> remember.
>
> On top of that: one open lot per symbol and direction. A thirty-minute entry-anchored
> dedup so a persistent score can't stack the same name. Sizing that trims to the
> account's real options buying power, so an expensive contract sizes *down* instead of
> rejecting. And a hard flatten at 15:50 Eastern, so a zero-DTE contract never reaches
> expiry.
>
> Notice what's missing: none of these depend on the signal being right.

### 1:33 — 1:55 · AI logic  *[Slide 5]*

> The AI is the least-trusted component, on purpose.
>
> A Claude layer argues bull and bear over the scored facts and reads the regime — but it
> can only ever turn a *go* into a *no-go*. It can never resurrect a trade the rules
> rejected. That makes the deterministic path a strict mathematical floor under the AI
> path, so you can run both and compare them honestly.
>
> And I'll be straight with you: the run I'm submitting used `rules_only`. We couldn't
> measure the model adding P&L, so we're not going to claim it did.

### 1:55 — 2:22 · Alpaca infrastructure  *[Slide 6 → cut to Terminal B]*

> Every order goes through the Alpaca **CLI**, not the SDK — which means the agent books
> the real broker fill, not a quote.
>
> *[cut to terminal]* Before it places anything, it verifies `alpaca doctor` resolves the
> paper endpoint — because the environment variable alone isn't proof; a profile's
> `live_trade` can route live.
>
> *[run `--once`]* Here's one live scan: score, noise band, gate verdict, risk verdict.
>
> *[show the journal record]* And here's the record it wrote — the complete reasoning
> behind one decision. Every scan produces one of these. **The audit trail is the product.**

### 2:22 — 2:50 · Results  *[Slide 7 → cut to Alpaca dashboard C]*

> From a brand-new hundred-thousand-dollar paper account, four sessions.
>
> *[show the dashboard]* Account PA38HG4D9653. [state the final number].
>
> And I'm not going to call that edge. Our own year of data says this signal doesn't have
> one, a single session supplies most of that gain, and the same distribution handed us
> minus ten point seven percent on day one.
>
> Four sessions isn't a sample. It's an anecdote with a good outcome.

### 2:50 — 3:00 · Close  *[Slide 8]*

> The equity curve is just what happened this week.
>
> What's reproducible is the part underneath — and the journal of every trade the agent
> refused. That's the submission.

---

## Recording checklist

- [ ] Regenerate deck + cover so every number matches the final account
- [ ] 1080p or better, landscape · deck full-screen, no window chrome
- [ ] Terminal font ≥ 18pt — judges may watch on a laptop
- [ ] **No API keys on screen at any point** (scan the recording back before uploading)
- [ ] Say the account ID out loud once — it's how judges tie the video to the P&L
- [ ] Upload unlisted to YouTube, paste the link in the submission form
- [ ] Keep it under 5:00; 3:00 is better

## If you're short on time

Record slides 1, 2, 5, 7 plus the journal-record shot. That's 90 seconds and still carries
the whole argument: no edge → structural risk → veto-only AI → honest result.
