# Video presentation — script, shot list, recording plan

**MP4, landscape, 3:00 (lablab's hard cap is 5:00).** Judges watch a lot of these back to
back. lablab's own guidance asks you to *"begin with an introduction, discuss your PDF
presentation, then showcase your project's functionalities"* — this script follows that
order: hook, slides, live demo.

Deck: `assets/deck.pdf` (9 slides — slide 9 is the required write-up, not narrated) ·
Cover: `assets/cover.png`

---

## Before you record

```bash
cd ~/alpaca-hackathon && assets/build_all.sh
```

That rebuilds the cover, the deck (pptx **and** the PDF lablab asks for) and the write-up,
pulling equity, per-session P&L and the fill counts straight from the account. Run it after
the final session so the video, the slides and the Alpaca dashboard all agree.

Set up three windows to cut between:

| # | Window | What's on it |
|---|---|---|
| A | Deck, presenter mode, full screen | slides 1-8 (9 is the write-up) |
| B | Terminal, 18pt+, dark theme | the demo commands below |
| C | Browser on the Alpaca paper dashboard | account `PA38HG4D9653` — equity + history |

**Mute notifications. Hide the bookmarks bar. Check no API key is on screen in window B or
C before you hit record** — `alpaca doctor` prints a key prefix, so crop it or scroll past.

---

## Demo commands (rehearse once, then record)

```bash
alpaca doctor                                    # the PAPER endpoint the agent verifies before every order
python -m src.agent --once                       # one live scan: score -> gate -> risk, dry-run, no orders
tail -n 1 logs/decisions-$(date +%F).jsonl | python3 -m json.tool   # the full reasoning for one decision
pytest -q                                        # 174 passed
```

The money shot is the **third** one. Find a record where `"go": false` and put that on
screen — a complete decision trail including the *refusals* is the thing no other
submission will show, and it is the single most persuasive frame in the video.

---

## Script

Read it conversationally. Bracketed lines are stage directions.

### 0:00 — 0:15 · Open on the number  *[Slide 1]*

> This is an autonomous options agent running on Alpaca paper. Brand-new account, funded
> at a hundred thousand dollars, and across three trading sessions it finished at a
> hundred and seventy-nine thousand. Up seventy-nine percent.
>
> Here's how it's built — and why every one of those trades is something you can check.

### 0:15 — 0:45 · Results  *[Slide 2 → cut to dashboard C]*

> Three sessions. The first one lost about eleven thousand. The next two made it back and
> then some — fourteen thousand, then seventy-five.
>
> *[cut to the Alpaca dashboard]* This is the actual account. Thirty-four entries,
> forty-three exits, on SPY, QQQ and IWM. Every one of them placed through the Alpaca CLI,
> so what you're looking at is real broker fills, not a mid-price model.
>
> And the book is flat at the end of every session. The agent never carries a zero-DTE
> contract into expiry.

### 0:45 — 1:05 · How it works  *[Slide 3]*

> One loop, six stages, and nobody in it. It scans Alpaca five-minute bars, scores them,
> runs the score through a gate, sizes the position against the account's real buying
> power, places the order through the CLI, and then manages the exit.
>
> Every control in that loop was validated against a year of real OPRA option bars — two
> hundred and sixty-four sessions, forty-five hundred alerts. What didn't survive that
> testing isn't in the code.

### 1:05 — 1:30 · Risk gates  *[Slide 4]*

> Risk here is structural before it's procedural. It trades single-leg long options only,
> so the maximum loss is the premium paid — and that ceiling is set by the instrument, not
> by code that has to remember.
>
> On top of that: one open lot per symbol and direction. A thirty-minute entry-anchored
> dedup, so a signal that stays hot can't stack the same name. Sizing that trims to the
> account's real options buying power, so an expensive contract sizes *down* instead of
> getting rejected. No entries after three PM, and a hard flatten at three-fifty.
>
> A hundred and seventy-four tests cover all of it.

### 1:30 — 1:50 · AI logic  *[Slide 5]*

> The AI layer is deliberately the least-trusted component. A Claude reasoning layer argues
> the bull and bear case over the scored facts and reads the regime — but it can only
> ever *remove* a trade. It cannot resurrect one the rules rejected.
>
> That's a structural guarantee, not a policy: the deterministic path is a strict subset
> of the AI path. And if the model is unreachable or returns something unparseable, the
> agent degrades to the deterministic gate and writes that down. It never trades on a
> response it couldn't read.

### 1:50 — 2:15 · Alpaca infrastructure  *[Slide 6 → cut to Terminal B]*

> Every order goes through the Alpaca CLI rather than the SDK.
>
> *[cut to terminal, run `alpaca doctor`]* Before it places anything, it confirms the CLI
> resolves to the paper endpoint — because the environment variable alone isn't proof; a
> profile's `live_trade` setting can route live. If that check fails, it refuses to trade.
>
> And on an ambiguous submit it reconciles by client order ID before it will ever
> resubmit, so a stalled order never becomes a duplicate or an orphan.
>
> *[run `--once`]* Here's one live scan — score, noise band, gate verdict, risk verdict.

### 2:15 — 2:45 · The audit trail  *[Slide 7 → terminal B]*

> And this is the part I'd actually point you at.
>
> *[show the journal record]* Every scan writes one of these: the score, each signal, the
> band state, the gate verdict, the risk verdict, and the order — or the absence of one.
>
> This record is a *refusal*. The score came in at 0.68 against a 0.70 threshold, so the
> gate said no and the agent didn't trade.
>
> Most agents show you the trades they took. This one shows you the ones it refused, and
> exactly why. That journal ships in the repo — clone it and check any decision against
> the record.

### 2:45 — 3:00 · Close  *[Slide 8]*

> A fresh hundred-thousand-dollar account, three sessions, up seventy-nine percent.
>
> Max loss capped by the instrument. An AI that can only remove risk. Every order through
> the Alpaca CLI. And every decision written down — including the ones it refused to make.
>
> Account PA38HG4D9653. Thanks for watching.

---

## Recording checklist

- [ ] Regenerate the cover and deck so every number matches the final account
- [ ] **MP4**, 1080p or better, landscape · deck full-screen, no window chrome
- [ ] Under 5:00 — lablab rejects longer. 3:00 is the target
- [ ] Terminal font ≥ 18pt — judges may watch on a laptop
- [ ] **No API keys on screen at any point** (scan the recording back before uploading)
- [ ] Say the account ID out loud once — it's how judges tie the video to the P&L
- [ ] Upload unlisted to YouTube, paste the link in the submission form

## If you're short on time

Record slides 1, 2, 7 plus the journal shot. Ninety seconds, and it still carries the whole
argument: the result, the machine that produced it, and the receipts.
