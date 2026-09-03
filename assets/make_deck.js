#!/usr/bin/env node
/* Build the hackathon slide deck (assets/deck.pptx) from the LIVE paper account.
 *
 *   node assets/make_deck.js
 *
 * Re-run before submitting so the results slide matches the account.
 * Needs: node, pptxgenjs (see PPTX_LIB below), and .env with the Alpaca paper keys.
 */
const fs = require("fs");
const path = require("path");
const https = require("https");

const ROOT = path.resolve(__dirname, "..");
const PPTX_LIB = process.env.PPTX_LIB || "/tmp/deckbuild/node_modules/pptxgenjs";
const pptxgen = require(PPTX_LIB);
const OUT = path.join(ROOT, "assets", "deck.pptx");

// --- palette (matches assets/cover.png) --------------------------------------
const BG = "0B0E14", CARD = "141922", LINE = "232A38";
const GOLD = "F0B429", RED = "E5484D";
const INK = "FFFFFF", INK2 = "A1A8B5", MUTED = "6B7686";
const SANS = "Arial", MONO = "Courier New";

const REPO = "github.com/KIBA0993/defined-risk-0dte-agent";
const ACCT = "PA38HG4D9653";

// ---------------------------------------------------------------- live data --
function env() {
  const d = {};
  for (const l of fs.readFileSync(path.join(ROOT, ".env"), "utf8").split("\n")) {
    const s = l.trim();
    if (s && !s.startsWith("#") && s.includes("=")) {
      const i = s.indexOf("=");
      d[s.slice(0, i).trim()] = s.slice(i + 1).trim().replace(/^["']|["']$/g, "");
    }
  }
  return d;
}
function api(p) {
  const e = env();
  return new Promise((res, rej) => {
    https.get({ host: "paper-api.alpaca.markets", path: p, headers: {
      "APCA-API-KEY-ID": e.ALPACA_API_KEY, "APCA-API-SECRET-KEY": e.ALPACA_SECRET_KEY } },
      r => { let b = ""; r.on("data", c => b += c); r.on("end", () => { try { res(JSON.parse(b)); } catch (x) { rej(x); } }); })
      .on("error", rej);
  });
}

// ------------------------------------------------------------------ helpers --
const usd = n => "$" + Math.round(n).toLocaleString("en-US");

function shell(slide, kicker) {
  slide.background = { color: BG };
  if (kicker) slide.addText(kicker, { x: 0.6, y: 0.42, w: 8, h: 0.3, isTextBox: true,
    fontFace: MONO, fontSize: 11, color: MUTED, charSpacing: 2 });
  slide.addText(REPO, { x: 8.0, y: 6.85, w: 4.73, h: 0.3, isTextBox: true, align: "right",
    fontFace: MONO, fontSize: 9, color: MUTED });
}

function title(slide, text, y = 0.95) {
  slide.addText(text, { x: 0.6, y, w: 12.1, h: 0.85, isTextBox: true, margin: 0,
    fontFace: SANS, fontSize: 34, bold: true, color: INK });
}

/** A card: tinted rounded rect + mono label + big value + note. No edge stripes.
 *  The note auto-fits the remaining height, so content can never spill the box. */
function card(slide, o) {
  slide.addShape("roundRect", { x: o.x, y: o.y, w: o.w, h: o.h,
    fill: { color: CARD }, line: { color: o.accent || LINE, width: 1 }, rectRadius: 0.08 });
  const padX = 0.32;
  let cy = o.y + 0.22;
  if (o.label) {
    slide.addText(o.label.toUpperCase(), { x: o.x + padX, y: cy, w: o.w - padX * 2, h: 0.24,
      isTextBox: true, margin: 0, fontFace: MONO, fontSize: 10, color: MUTED, charSpacing: 1.4 });
    cy += 0.32;
  }
  if (o.value) {
    const vh = o.valueH || 0.6;
    slide.addText(o.value, { x: o.x + padX, y: cy, w: o.w - padX * 2, h: vh,
      isTextBox: true, margin: 0, fontFace: SANS, fontSize: o.valueSize || 40,
      bold: true, color: o.valueColor || INK });
    cy += vh + 0.06;
  }
  if (o.note) {
    const nh = o.y + o.h - 0.18 - cy;
    slide.addText(o.note, { x: o.x + padX, y: cy, w: o.w - padX * 2, h: nh,
      isTextBox: true, margin: 0, fontFace: SANS, fontSize: o.noteSize || 12,
      color: INK2, lineSpacingMultiple: 1.2, valign: "top" });
  }
}

// -------------------------------------------------------------------- build --
async function main() {
  const acct = await api("/v2/account");
  const hist = await api("/v2/account/portfolio/history?period=1W&timeframe=1D");
  const equity = parseFloat(acct.equity);
  const pct = (equity / 100000 - 1) * 100;
  const closes = hist.equity.filter(v => v);
  const curve = closes.concat([equity]);
  const labels = ["Aug 28", "Aug 31", "Sep 1", "Sep 2", "Sep 3"].slice(-curve.length);

  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";                     // 13.333 x 7.5
  pres.author = "KIBA0993";
  pres.title = "The Agent That Failed Its Own Backtest";

  // ---- 1 title -------------------------------------------------------------
  let s = pres.addSlide();
  shell(s, "Alpaca AI Trading Agents Hackathon  ·  Sept 2026");
  s.addText([
    { text: "The Agent That", options: { color: INK, breakLine: true } },
    { text: "Failed Its Own Backtest", options: { color: GOLD } },
  ], { x: 0.6, y: 1.55, w: 12.1, h: 2.5, isTextBox: true, margin: 0,
       fontFace: SANS, fontSize: 54, bold: true, lineSpacingMultiple: 1.02 });
  s.addText("Defined-risk 0DTE options on Alpaca. Max loss fixed by the instrument —\nnot by code that has to remember.",
    { x: 0.6, y: 4.05, w: 9.4, h: 0.9, isTextBox: true, margin: 0,
      fontFace: SANS, fontSize: 17, color: INK2, lineSpacingMultiple: 1.35 });
  card(s, { x: 0.6, y: 5.05, w: 3.75, h: 1.6, label: "Backtest verdict",
    value: "−$8 / trade", valueSize: 24, valueColor: RED, valueH: 0.45,
    note: "248 sessions, real OPRA tape", noteSize: 11 });
  card(s, { x: 4.6, y: 5.05, w: 3.75, h: 1.6, label: "Live Alpaca paper",
    value: (pct >= 0 ? "+" : "") + pct.toFixed(1) + "%", valueSize: 24, valueColor: GOLD, valueH: 0.45,
    note: "$100,000 → " + usd(equity) + "\n4 sessions", noteSize: 11 });
  card(s, { x: 8.6, y: 5.05, w: 4.13, h: 1.6, label: "Paper account",
    value: ACCT, valueSize: 20, valueColor: INK, valueH: 0.45,
    note: "Opened fresh for this event at $100,000", noteSize: 11 });
  s.addNotes("Every agent in this hackathon opens with an edge claim. This one opens with a refutation — and the live number makes the point sharper, not softer.");

  // ---- 2 the verdict -------------------------------------------------------
  s = pres.addSlide();
  shell(s, "01  ·  The research");
  title(s, "We tried to break our own signal. We succeeded.");
  s.addText("A year of real Alpaca OPRA option bars, ~248 sessions, SPY / QQQ / IWM. Every test reviewed by a second adversarial pass; these are the numbers that survived.",
    { x: 0.6, y: 1.85, w: 11.6, h: 0.6, isTextBox: true, margin: 0,
      fontFace: SANS, fontSize: 14, color: INK2, lineSpacingMultiple: 1.3 });
  card(s, { x: 0.6, y: 2.7, w: 3.85, h: 2.1, label: "Simulated P&L",
    value: "−$8", valueColor: RED, note: "per trade, ~40% win rate. Both calls and puts underwater." });
  card(s, { x: 4.72, y: 2.7, w: 3.85, h: 2.1, label: "Hit rate, session-weighted",
    value: "49.4%", valueColor: RED, note: "at +15 min. 49.2% at 30, 47.8% at 60 — worse with horizon." });
  card(s, { x: 8.84, y: 2.7, w: 3.89, h: 2.1, label: "The move vs the cost",
    value: "11 bp", valueColor: RED, note: "median 30-min move. Crossing the spread costs ~20 bp before decay." });
  s.addText("“The apparent 52% was a size-weighting artifact of a handful of busy sessions. The recurring trap: any rule that cuts position count flatters a negative-expectancy book.”",
    { x: 0.6, y: 5.15, w: 12.1, h: 0.9, isTextBox: true, margin: 0, italic: true,
      fontFace: SANS, fontSize: 15, color: INK, lineSpacingMultiple: 1.3 });
  s.addText("docs/research.md — the full autopsy, including every filter we retired",
    { x: 0.6, y: 6.15, w: 8, h: 0.3, isTextBox: true, margin: 0, fontFace: MONO, fontSize: 10, color: MUTED });
  s.addNotes("We removed the exit rule entirely and asked the cleanest question: does the signal predict direction? Once each session gets an equal vote, it is below a coin flip.");

  // ---- 3 architecture ------------------------------------------------------
  s = pres.addSlide();
  shell(s, "02  ·  The design");
  title(s, "So we built what survives not having one");
  const steps = [
    ["SCAN", "Alpaca 5m bars"], ["SCORE", "VWAP, OR, RSI,\nEMA, rel-vol"],
    ["GATE", "band + regime\n(+ optional veto)"], ["RISK", "caps, cooldowns,\nsizing"],
    ["EXECUTE", "ATM long via\nAlpaca CLI"], ["MANAGE", "ladder exits,\n15:50 flatten"],
  ];
  const bw = 1.86, gap = 0.22;
  steps.forEach(([k, v], i) => {
    const x = 0.6 + i * (bw + gap);
    s.addShape("roundRect", { x, y: 2.15, w: bw, h: 1.5, fill: { color: CARD },
      line: { color: i === 3 ? GOLD : LINE, width: 1 }, rectRadius: 0.08 });
    s.addText(k, { x, y: 2.32, w: bw, h: 0.3, isTextBox: true, align: "center",
      fontFace: MONO, fontSize: 12, bold: true, color: i === 3 ? GOLD : INK });
    s.addText(v, { x: x + 0.1, y: 2.68, w: bw - 0.2, h: 0.85, isTextBox: true, align: "center",
      fontFace: SANS, fontSize: 10, color: INK2, lineSpacingMultiple: 1.2 });
    if (i < steps.length - 1) s.addText("›", { x: x + bw, y: 2.6, w: gap, h: 0.4,
      isTextBox: true, align: "center", fontFace: SANS, fontSize: 16, color: MUTED });
  });
  card(s, { x: 0.6, y: 3.95, w: 3.85, h: 2.45, label: "Defined by construction",
    value: "Max loss\n= premium", valueSize: 20, valueColor: GOLD, valueH: 0.95,
    note: "Single-leg long only. No path to a blow-up — the point when there is no edge to lean on." });
  card(s, { x: 4.72, y: 3.95, w: 3.85, h: 2.45, label: "AI scoped to risk-off",
    value: "It can only\nsay no", valueSize: 20, valueColor: GOLD, valueH: 0.95,
    note: "We could not measure the model adding P&L, so we never let it manufacture conviction." });
  card(s, { x: 8.84, y: 3.95, w: 3.89, h: 2.45, label: "Radical transparency",
    value: "Every refusal\njournaled", valueSize: 20, valueColor: GOLD, valueH: 0.95,
    note: "One JSONL record per scan with the full reasoning. The audit trail is the product." });
  s.addNotes("Three consequences of having no edge: fix the loss structurally, scope the AI to removal, and make the whole thing auditable.");

  // ---- 4 risk gates --------------------------------------------------------
  s = pres.addSlide();
  shell(s, "03  ·  Risk gates");
  title(s, "Risk is structural first, procedural second");
  s.addShape("roundRect", { x: 0.6, y: 1.88, w: 12.13, h: 0.78, fill: { color: CARD },
    line: { color: GOLD, width: 1 }, rectRadius: 0.08 });
  s.addText([
    { text: "Single-leg long options only.  ", options: { color: INK, bold: true } },
    { text: "Maximum loss is the premium paid — enforced by the instrument, not by code that has to remember.", options: { color: INK2 } },
  ], { x: 0.9, y: 1.88, w: 11.5, h: 0.78, isTextBox: true, margin: 0, valign: "middle",
       fontFace: SANS, fontSize: 14 });
  const gates = [
    ["One-lot guard", "Never a second open lot on a (symbol, direction) already held."],
    ["One direction / underlying", "No simultaneous call and put on the same symbol."],
    ["Entry dedup · 30 min", "Entry-anchored, so a persistent score cannot stack the same name."],
    ["Scoped cooldown · 15 min", "Re-entry block armed only by a time_stop close."],
    ["Adaptive sizing", "Target trimmed to the account's real options buying power — pricey contracts size down, never reject."],
    ["Session window", "No entry after 15:00 ET. Hard flatten 15:50 — a 0DTE never reaches expiry."],
  ];
  gates.forEach(([k, v], i) => {
    const col = i % 3, row = Math.floor(i / 3);
    card(s, { x: 0.6 + col * 4.12, y: 2.92 + row * 1.72, w: 3.89, h: 1.52,
      label: k, note: v, noteSize: 11, noteH: 0.95 });
  });
  s.addText("Exits ladder both ways:  +40% scales out half and trails the runner  ·  −20% sells half, −40% the rest, −65% backstop  ·  30-min time stop  ·  EOD flatten  ·  175 tests.",
    { x: 0.6, y: 6.42, w: 12.13, h: 0.5, isTextBox: true, margin: 0,
      fontFace: SANS, fontSize: 11, color: INK2 });
  s.addNotes("Note what is missing: nothing here depends on the signal being right. These gates are what you build when you assume it is not.");

  // ---- 5 AI logic ----------------------------------------------------------
  s = pres.addSlide();
  shell(s, "04  ·  AI logic");
  title(s, "The AI is the least-trusted component, by design");
  const flow = [
    ["Scored signal", "VWAP, OR break, RSI,\nEMA, relative volume", INK],
    ["Rules gate", "score ≥ 0.70, outside the\nhalf-OR band, regime agrees", INK],
    ["Claude veto", "bull / bear debate,\nregime read — no-go only", GOLD],
    ["Order", "ATM long via the\nAlpaca CLI", INK],
  ];
  flow.forEach(([k, v, c], i) => {
    const x = 0.6 + i * 3.18;
    s.addShape("roundRect", { x, y: 2.05, w: 2.9, h: 1.55, fill: { color: CARD },
      line: { color: c === GOLD ? GOLD : LINE, width: 1 }, rectRadius: 0.08 });
    s.addText(k, { x, y: 2.24, w: 2.9, h: 0.32, isTextBox: true, align: "center",
      fontFace: SANS, fontSize: 14, bold: true, color: c });
    s.addText(v, { x: x + 0.15, y: 2.64, w: 2.6, h: 0.8, isTextBox: true, align: "center",
      fontFace: SANS, fontSize: 10, color: INK2, lineSpacingMultiple: 1.2 });
    if (i < 3) s.addText("›", { x: x + 2.9, y: 2.55, w: 0.28, h: 0.4, isTextBox: true,
      align: "center", fontFace: SANS, fontSize: 18, color: MUTED });
  });
  card(s, { x: 0.6, y: 3.95, w: 5.95, h: 1.95, label: "The one-way rule",
    value: "go → no-go only", valueSize: 22, valueColor: GOLD, valueH: 0.45,
    note: "The model can never resurrect a trade the rules rejected, so rules_only is a strict mathematical floor under the AI path. Missing key or bad JSON degrades to rules and logs it — it never fails open.",
    noteSize: 11, noteH: 0.75 });
  card(s, { x: 6.78, y: 3.95, w: 5.95, h: 1.95, label: "What we submitted",
    value: "decision_mode: rules_only", valueSize: 17, valueColor: INK, valueH: 0.45,
    note: "The scored run used the deterministic stack. The LLM gate ships tested and runnable (--decision-mode llm) but was not in the scored path — we say which arm produced the numbers.",
    noteSize: 11, noteH: 0.75 });
  s.addText("“We never let it manufacture conviction we couldn't measure — because we measured, and it wasn't there.”",
    { x: 0.6, y: 6.10, w: 12.13, h: 0.5, isTextBox: true, margin: 0, italic: true,
      fontFace: SANS, fontSize: 14, color: INK });
  s.addNotes("Most submissions say LLM proposes, code decides. Ours goes further: the LLM is structurally incapable of adding risk, and we disclose that the scored arm ran without it.");

  // ---- 6 Alpaca infrastructure --------------------------------------------
  s = pres.addSlide();
  shell(s, "05  ·  Alpaca infrastructure");
  title(s, "Built on Alpaca's CLI, not just the SDK");
  const infra = [
    ["Orders via the Alpaca CLI", "alpaca order submit, then poll to a terminal state. The agent books the real broker fill, not a quote — so cash P&L is buy-fill vs sell-fill."],
    ["Paper endpoint, proven", "Refuses to place anything unless `alpaca doctor` resolves paper-api.alpaca.markets. The env var alone isn't proof — a profile's live_trade can route live."],
    ["Idempotent submits", "On an ambiguous failure it reconciles by --client-order-id via `alpaca order get-by-client-id` before ever resubmitting. No duplicate, no orphan."],
    ["Unattended + auditable", "Market data (5m bars, contract discovery, ATM quotes) via the Alpaca SDK. Isolated Docker container on a NAS, one session per weekday, JSONL journal per scan."],
  ];
  infra.forEach(([k, v], i) => {
    const col = i % 2, row = Math.floor(i / 2);
    card(s, { x: 0.6 + col * 6.18, y: 1.95 + row * 2.15, w: 5.95, h: 1.95,
      label: k, note: v, noteSize: 12, noteH: 1.25 });
  });
  s.addText("Requirement met: projects must use Alpaca's MCP server or its CLI tools.   —   this agent places every order through the CLI.",
    { x: 0.6, y: 6.35, w: 12.13, h: 0.4, isTextBox: true, margin: 0,
      fontFace: MONO, fontSize: 11, color: GOLD });
  s.addNotes("The CLI is not a box-tick. It is why the P&L on the slide after this is broker truth rather than a mid-price model.");

  // ---- 7 results -----------------------------------------------------------
  s = pres.addSlide();
  shell(s, "06  ·  Results");
  title(s, "Four sessions, reported honestly");
  s.addShape("roundRect", { x: 0.6, y: 1.9, w: 7.1, h: 3.5, fill: { color: CARD },
    line: { color: LINE, width: 1 }, rectRadius: 0.08 });
  s.addText("EQUITY · ALPACA PAPER " + ACCT, { x: 0.9, y: 2.08, w: 6.5, h: 0.28,
    isTextBox: true, margin: 0, fontFace: MONO, fontSize: 10, color: MUTED, charSpacing: 1.2 });
  s.addChart(pres.ChartType.line,
    [{ name: "Equity", labels, values: curve }],
    { x: 0.78, y: 2.42, w: 6.74, h: 2.85,
      chartColors: [GOLD], lineSize: 3, lineSmooth: false,
      lineDataSymbol: "circle", lineDataSymbolSize: 7,
      lineDataSymbolLineColor: BG, lineDataSymbolLineSize: 2,
      showLegend: false, showTitle: false,
      catAxisLabelColor: MUTED, catAxisLabelFontSize: 10, catAxisLabelFontFace: MONO,
      catAxisLineShow: false, catGridLine: { style: "none" },
      valAxisLabelColor: MUTED, valAxisLabelFontSize: 10, valAxisLabelFontFace: MONO,
      valAxisLineShow: false, valAxisLabelFormatCode: "$#,##0,\\K",
      valAxisMinVal: 80000, valAxisMaxVal: 220000, valAxisMajorUnit: 20000,
      valGridLine: { color: LINE, size: 1 },
      plotArea: { fill: { color: CARD } }, chartArea: { fill: { color: CARD } } });
  s.addText("REALIZED BY SESSION", { x: 7.95, y: 2.08, w: 4.78, h: 0.28, isTextBox: true,
    margin: 0, fontFace: MONO, fontSize: 10, color: MUTED, charSpacing: 1.2 });
  const rows = [["Aug 31", "−$10,636", "19 closes", "31.6% win", RED],
                ["Sep 1", "+$14,400", "9 closes", "88.9% win", GOLD],
                ["Sep 3", "+$71,400", "13 closes", "76.9% win", GOLD]];
  rows.forEach(([day, pnl, n, win, c], i) => {
    const y = 2.45 + i * 0.62;
    s.addShape("roundRect", { x: 7.95, y, w: 4.78, h: 0.54, fill: { color: CARD },
      line: { color: LINE, width: 1 }, rectRadius: 0.06 });
    s.addText(day, { x: 8.15, y, w: 1.05, h: 0.54, isTextBox: true, margin: 0, valign: "middle",
      fontFace: SANS, fontSize: 13, bold: true, color: INK });
    s.addText(pnl, { x: 9.2, y, w: 1.45, h: 0.54, isTextBox: true, margin: 0, valign: "middle",
      align: "right", fontFace: MONO, fontSize: 13, bold: true, color: c });
    s.addText(n, { x: 10.75, y, w: 1.0, h: 0.54, isTextBox: true, margin: 0, valign: "middle",
      align: "right", fontFace: SANS, fontSize: 11, color: INK2 });
    s.addText(win, { x: 11.75, y, w: 0.85, h: 0.54, isTextBox: true, margin: 0, valign: "middle",
      align: "right", fontFace: SANS, fontSize: 11, color: INK2 });
  });
  card(s, { x: 7.95, y: 4.42, w: 4.78, h: 0.98, label: "Net, live",
    value: (pct >= 0 ? "+" : "") + pct.toFixed(1) + "%  ·  " + usd(equity),
    valueSize: 21, valueColor: GOLD, valueH: 0.42 });
  s.addShape("roundRect", { x: 0.6, y: 5.62, w: 12.13, h: 1.05, fill: { color: CARD },
    line: { color: RED, width: 1 }, rectRadius: 0.08 });
  s.addText([
    { text: "We won't call that edge.  ", options: { color: INK, bold: true } },
    { text: "Our own data says this signal has none, one session supplies most of the gain, and the same distribution handed us −10.7% on day one. Four sessions is not a sample — it is an anecdote with a good outcome.", options: { color: INK2 } },
  ], { x: 0.9, y: 5.62, w: 11.5, h: 1.05, isTextBox: true, margin: 0, valign: "middle",
       fontFace: SANS, fontSize: 13, lineSpacingMultiple: 1.25 });
  s.addNotes("Judges will see the number. What they should also see is that we are the only ones telling them how much of it is variance.");

  // ---- 8 closing -----------------------------------------------------------
  s = pres.addSlide();
  shell(s, "What we are actually claiming");
  s.addText([
    { text: "The equity curve is just", options: { color: INK, breakLine: true } },
    { text: "what happened this week.", options: { color: GOLD } },
  ], { x: 0.6, y: 1.5, w: 12.1, h: 1.9, isTextBox: true, margin: 0,
       fontFace: SANS, fontSize: 46, bold: true, lineSpacingMultiple: 1.05 });
  const claims = [
    ["Max loss fixed by the instrument", "not by code that has to remember"],
    ["An AI that is only allowed to say no", "scoped to what we could actually measure"],
    ["Gates that hold when the P&L is ugly", "they never depend on the signal being right"],
    ["A journal of every trade it refused", "the audit trail is the deliverable"],
  ];
  claims.forEach(([k, v], i) => {
    const y = 3.75 + i * 0.66;
    s.addText("—", { x: 0.6, y, w: 0.35, h: 0.4, isTextBox: true, margin: 0,
      fontFace: SANS, fontSize: 15, color: GOLD });
    s.addText([
      { text: k, options: { color: INK, bold: true } },
      { text: "   " + v, options: { color: INK2 } },
    ], { x: 1.0, y, w: 11.7, h: 0.4, isTextBox: true, margin: 0, fontFace: SANS, fontSize: 15 });
  });
  s.addText("Paper trading only. Hypothetical results; not investment advice.",
    { x: 0.6, y: 6.85, w: 7, h: 0.3, isTextBox: true, margin: 0,
      fontFace: MONO, fontSize: 9, color: MUTED });
  s.addNotes("Close on the claim, not the number. The number is this week; the architecture is the submission.");

  await pres.writeFile({ fileName: OUT });
  console.log(`wrote ${OUT}  —  equity ${usd(equity)} (${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)`);
}

main().catch(e => { console.error(e); process.exit(1); });
