#!/usr/bin/env node
/* Build the hackathon slide deck (assets/deck.pptx) from the LIVE paper account.
 *
 *   node assets/make_deck.js
 *
 * Every figure on the results slide - equity, per-session P&L, entry/exit counts,
 * session count - is pulled at build time. Nothing is hardcoded, so re-running
 * this before submitting can never leave a stale number behind.
 *
 * Needs: node, pptxgenjs (see PPTX_LIB), and .env with the Alpaca paper keys.
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

const REPO = "github.com/KIBA0993/Alpaca-Hackathon";
const ACCT = "PA38HG4D9653";
const START = 100000;

const dayLabel = d => new Date(d + "T12:00:00Z").toLocaleDateString("en-US",
  { month: "short", day: "numeric", timeZone: "UTC" });

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

/** Sessions that actually filled orders, each with its realised P&L and fill counts. */
async function loadBook() {
  const acct = await api("/v2/account");
  const hist = await api("/v2/account/portfolio/history?period=1M&timeframe=1D");
  const orders = await api("/v2/orders?status=all&after=2026-08-28T00:00:00Z&limit=500&direction=asc");
  const equity = parseFloat(acct.equity);

  const fills = orders.filter(o => o.status === "filled" && o.filled_at);
  const byDay = new Map();
  for (const o of fills) {
    const d = o.filled_at.slice(0, 10);
    const r = byDay.get(d) || { day: d, buys: 0, sells: 0, contracts: 0 };
    o.side === "buy" ? r.buys++ : r.sells++;
    r.contracts += parseInt(o.filled_qty, 10);
    byDay.set(d, r);
  }
  const sessions = [...byDay.values()].sort((a, b) => a.day.localeCompare(b.day));

  // Realised P&L per session: the non-zero daily rows, plus today's if it has not settled yet.
  const pls = hist.profit_loss.filter((v, i) => hist.equity[i] && Math.abs(v) > 0.005);
  const today = equity - parseFloat(acct.last_equity);
  if (pls.length < sessions.length && Math.abs(today) > 0.005) pls.push(today);
  sessions.forEach((s, i) => { s.pnl = pls[i]; });

  // Curve = the opening balance, then the closing equity after each TRADED session.
  // Built from the sessions themselves so the point count can never drift from the
  // label count (a mismatch produces a chart PowerPoint/LibreOffice refuse to open).
  let run = START;
  const curve = [START].concat(sessions.map(r => (run += (r.pnl || 0))));
  const labels = ["Start"].concat(sessions.map(r => dayLabel(r.day)));
  return { equity, pct: (equity / START - 1) * 100, sessions, curve, labels,
           entries: fills.filter(o => o.side === "buy").length,
           exits: fills.filter(o => o.side === "sell").length };
}

/** What the repo's logs/ directory actually contains, so slide 7 cannot overclaim. */
function journalStats() {
  const dir = path.join(ROOT, "logs");
  if (!fs.existsSync(dir)) return { records: 0, days: 0 };
  const files = fs.readdirSync(dir).filter(f => f.endsWith(".jsonl"));
  let records = 0;
  for (const f of files) {
    records += fs.readFileSync(path.join(dir, f), "utf8").split("\n").filter(l => l.trim()).length;
  }
  return { records, days: files.length };
}

// ------------------------------------------------------------------ helpers --
const usd = n => (n < 0 ? "-$" : "$") + Math.round(Math.abs(n)).toLocaleString("en-US");
const signed = n => (n >= 0 ? "+" : "") + n.toFixed(1) + "%";
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

/** Card: tinted rounded rect + mono label + value + note. The note auto-fits the
 *  remaining height, so text can never spill the box. No edge stripes. */
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
    slide.addText(o.note, { x: o.x + padX, y: cy, w: o.w - padX * 2,
      h: o.y + o.h - 0.18 - cy, isTextBox: true, margin: 0, fontFace: SANS,
      fontSize: o.noteSize || 12, color: INK2, lineSpacingMultiple: 1.2, valign: "top" });
  }
}

/** A dense bullet column, used by the write-up slide. */
function bullets(slide, x, y, w, h, heading, items) {
  slide.addShape("roundRect", { x, y, w, h, fill: { color: CARD },
    line: { color: LINE, width: 1 }, rectRadius: 0.08 });
  slide.addText(heading, { x: x + 0.28, y: y + 0.2, w: w - 0.56, h: 0.3, isTextBox: true,
    margin: 0, fontFace: SANS, fontSize: 15, bold: true, color: GOLD });
  slide.addText(items.map((s, i) => ({
    text: s, options: { bullet: true, breakLine: i < items.length - 1 } })),
    { x: x + 0.28, y: y + 0.62, w: w - 0.56, h: h - 0.82, isTextBox: true, margin: 0,
      fontFace: SANS, fontSize: 9.5, color: INK2, lineSpacingMultiple: 1.14,
      paraSpaceAfter: 4, valign: "top" });
}

/** A row of connected process boxes. */
function flow(slide, steps, y, h, accentIdx) {
  const gap = 0.22, bw = (12.13 - gap * (steps.length - 1)) / steps.length;
  steps.forEach(([k, v], i) => {
    const x = 0.6 + i * (bw + gap), hot = i === accentIdx;
    slide.addShape("roundRect", { x, y, w: bw, h, fill: { color: CARD },
      line: { color: hot ? GOLD : LINE, width: 1 }, rectRadius: 0.08 });
    slide.addText(k, { x, y: y + 0.18, w: bw, h: 0.3, isTextBox: true, align: "center",
      fontFace: MONO, fontSize: 12, bold: true, color: hot ? GOLD : INK });
    slide.addText(v, { x: x + 0.1, y: y + 0.54, w: bw - 0.2, h: h - 0.68, isTextBox: true,
      align: "center", fontFace: SANS, fontSize: 10, color: INK2, lineSpacingMultiple: 1.2 });
    if (i < steps.length - 1) slide.addText("›", { x: x + bw, y: y + h / 2 - 0.2,
      w: gap, h: 0.4, isTextBox: true, align: "center", fontFace: SANS, fontSize: 16, color: MUTED });
  });
}

// -------------------------------------------------------------------- build --
async function main() {
  const bk = await loadBook();

  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";                     // 13.333 x 7.5
  pres.author = "KIBA0993";
  pres.title = "Every Trade Provable. Every Loss Capped.";

  // ---- 1 title -------------------------------------------------------------
  let s = pres.addSlide();
  shell(s, "Alpaca AI Trading Agents Hackathon  ·  Sept 2026");
  s.addText([
    { text: "Every Trade Provable.", options: { color: INK, breakLine: true } },
    { text: "Every Loss Capped.", options: { color: GOLD } },
  ], { x: 0.6, y: 1.5, w: 12.1, h: 2.4, isTextBox: true, margin: 0,
       fontFace: SANS, fontSize: 52, bold: true, lineSpacingMultiple: 1.04 });
  s.addText("An autonomous 0DTE options agent on Alpaca. Max loss capped by construction,\nand every decision it makes is journaled.",
    { x: 0.6, y: 4.05, w: 10.5, h: 0.9, isTextBox: true, margin: 0,
      fontFace: SANS, fontSize: 17, color: INK2, lineSpacingMultiple: 1.35 });
  card(s, { x: 0.6, y: 5.05, w: 4.3, h: 1.6, label: `Live Alpaca paper · ${bk.sessions.length} sessions`,
    value: signed(bk.pct), valueSize: 30, valueColor: GOLD, valueH: 0.5,
    note: `$100,000 → ${usd(bk.equity)}`, noteSize: 12 });
  card(s, { x: 5.12, y: 5.05, w: 3.6, h: 1.6, label: "Max loss",
    value: "= premium paid", valueSize: 19, valueColor: INK, valueH: 0.5,
    note: "Single-leg long only", noteSize: 12 });
  card(s, { x: 8.94, y: 5.05, w: 3.79, h: 1.6, label: "Paper account",
    value: ACCT, valueSize: 19, valueColor: INK, valueH: 0.5,
    note: "Opened fresh for this event at $100,000", noteSize: 12 });
  s.addNotes("Lead with the number. A brand-new $100k paper account, and the agent is up "
    + signed(bk.pct) + " across " + bk.sessions.length + " trading sessions.");

  // ---- 2 results -----------------------------------------------------------
  s = pres.addSlide();
  shell(s, "01  ·  Results");
  title(s, `A fresh $100,000 account, ${bk.sessions.length} sessions, ${signed(bk.pct)}`);
  s.addShape("roundRect", { x: 0.6, y: 1.9, w: 7.1, h: 3.5, fill: { color: CARD },
    line: { color: LINE, width: 1 }, rectRadius: 0.08 });
  s.addText("EQUITY · ALPACA PAPER " + ACCT, { x: 0.9, y: 2.08, w: 6.5, h: 0.28,
    isTextBox: true, margin: 0, fontFace: MONO, fontSize: 10, color: MUTED, charSpacing: 1.2 });
  const lo = Math.floor(Math.min(...bk.curve) / 20000) * 20000;
  const hi = Math.ceil(Math.max(...bk.curve) / 20000) * 20000;
  s.addChart(pres.ChartType.line, [{ name: "Equity", labels: bk.labels, values: bk.curve }],
    { x: 0.78, y: 2.42, w: 6.74, h: 2.85,
      chartColors: [GOLD], lineSize: 3, lineSmooth: false,
      lineDataSymbol: "circle", lineDataSymbolSize: 7,
      lineDataSymbolLineColor: BG, lineDataSymbolLineSize: 2,
      showLegend: false, showTitle: false,
      catAxisLabelColor: MUTED, catAxisLabelFontSize: 10, catAxisLabelFontFace: MONO,
      catAxisLineShow: false, catGridLine: { style: "none" },
      valAxisLabelColor: MUTED, valAxisLabelFontSize: 10, valAxisLabelFontFace: MONO,
      valAxisLineShow: false, valAxisLabelFormatCode: "$#,##0,\\K",
      valAxisMinVal: lo, valAxisMaxVal: hi, valAxisMajorUnit: 20000,
      valGridLine: { color: LINE, size: 1 },
      plotArea: { fill: { color: CARD } }, chartArea: { fill: { color: CARD } } });

  s.addText("REALISED BY SESSION", { x: 7.95, y: 2.08, w: 4.78, h: 0.28, isTextBox: true,
    margin: 0, fontFace: MONO, fontSize: 10, color: MUTED, charSpacing: 1.2 });
  bk.sessions.forEach((r, i) => {
    const y = 2.45 + i * 0.62;
    s.addShape("roundRect", { x: 7.95, y, w: 4.78, h: 0.54, fill: { color: CARD },
      line: { color: LINE, width: 1 }, rectRadius: 0.06 });
    s.addText(dayLabel(r.day), { x: 8.15, y, w: 1.1, h: 0.54, isTextBox: true, margin: 0,
      valign: "middle", fontFace: SANS, fontSize: 13, bold: true, color: INK });
    s.addText(r.pnl >= 0 ? "+" + usd(r.pnl) : usd(r.pnl),
      { x: 9.25, y, w: 1.5, h: 0.54, isTextBox: true, margin: 0, valign: "middle",
        align: "right", fontFace: MONO, fontSize: 13, bold: true,
        color: r.pnl >= 0 ? GOLD : RED });
    s.addText(`${r.buys} in / ${r.sells} out`, { x: 10.8, y, w: 1.8, h: 0.54, isTextBox: true,
      margin: 0, valign: "middle", align: "right", fontFace: SANS, fontSize: 11, color: INK2 });
  });
  card(s, { x: 7.95, y: 2.45 + bk.sessions.length * 0.62 + 0.12, w: 4.78, h: 0.98,
    label: "Net", value: `${signed(bk.pct)}  ·  ${usd(bk.equity)}`,
    valueSize: 21, valueColor: GOLD, valueH: 0.42 });
  s.addText(`${bk.entries} entries and ${bk.exits} exits on SPY / QQQ / IWM, every one placed through the Alpaca CLI and booked at the real broker fill. Positions are flat at the close of every session - the agent never carries a 0DTE into expiry.`,
    { x: 0.6, y: 5.65, w: 12.13, h: 0.8, isTextBox: true, margin: 0,
      fontFace: SANS, fontSize: 13, color: INK2, lineSpacingMultiple: 1.25 });
  s.addNotes("Every figure on this slide is pulled from the Alpaca account at build time - "
    + "nothing here is typed in by hand.");

  // ---- 3 how it works ------------------------------------------------------
  s = pres.addSlide();
  shell(s, "02  ·  How it works");
  title(s, "One loop, six stages, no human in it");
  flow(s, [["SCAN", "Alpaca 5m bars"], ["SCORE", "VWAP, OR, RSI,\nEMA, rel-vol"],
           ["GATE", "noise band +\nregime"], ["RISK", "caps, cooldowns,\nsizing"],
           ["EXECUTE", "ATM long via\nAlpaca CLI"], ["MANAGE", "ladder exits,\n15:50 flatten"]],
       2.15, 1.5, 3);
  card(s, { x: 0.6, y: 3.95, w: 3.85, h: 2.45, label: "Defined by construction",
    value: "Max loss\n= premium", valueSize: 20, valueColor: GOLD, valueH: 0.95,
    note: "Single-leg long only. The ceiling is set by the instrument, not by code that has to remember." });
  card(s, { x: 4.72, y: 3.95, w: 3.85, h: 2.45, label: "Validated, not guessed",
    value: "264 sessions", valueSize: 20, valueColor: GOLD, valueH: 0.95,
    note: "of real OPRA option bars and 4,544 alerts stand behind every control that ships." });
  card(s, { x: 8.84, y: 3.95, w: 3.89, h: 2.45, label: "Fully autonomous",
    value: "Docker,\nunattended", valueSize: 20, valueColor: GOLD, valueH: 0.95,
    note: "One session per weekday in an isolated container. Nobody approves a trade." });
  s.addNotes("Exit management runs before entry on every pass - the book is always managed first.");

  // ---- 4 risk gates --------------------------------------------------------
  s = pres.addSlide();
  shell(s, "03  ·  Risk gates");
  title(s, "Risk is structural before it is procedural");
  s.addShape("roundRect", { x: 0.6, y: 1.88, w: 12.13, h: 0.78, fill: { color: CARD },
    line: { color: GOLD, width: 1 }, rectRadius: 0.08 });
  s.addText([
    { text: "Single-leg long options only.  ", options: { color: INK, bold: true } },
    { text: "Maximum loss is the premium paid - enforced by the instrument, not by code that has to remember.", options: { color: INK2 } },
  ], { x: 0.9, y: 1.88, w: 11.5, h: 0.78, isTextBox: true, margin: 0, valign: "middle",
       fontFace: SANS, fontSize: 14 });
  [
    ["One-lot guard", "Never a second open lot on a (symbol, direction) already held."],
    ["One direction / underlying", "No simultaneous call and put on the same symbol."],
    ["Entry dedup · 30 min", "Entry-anchored, so a persistent score cannot stack the same name."],
    ["Adaptive sizing", "Trimmed to the account's real options buying power - an expensive contract sizes down, never rejects."],
    ["Session window", "No entry after 15:00 ET. Hard flatten at 15:50 - a 0DTE never reaches expiry."],
    ["Orphan sweep", "Any option position the agent did not open is flattened at startup and at EOD."],
  ].forEach(([k, v], i) => {
    card(s, { x: 0.6 + (i % 3) * 4.12, y: 2.92 + Math.floor(i / 3) * 1.72, w: 3.89, h: 1.52,
      label: k, note: v, noteSize: 11 });
  });
  s.addText("Exits ladder in both directions:  +40% scales out half and trails the runner by 40% of its peak gain  ·  -20% sells half, -40% the rest  ·  a 30-minute stop closes anything still under water  ·  15:50 flatten.",
    { x: 0.6, y: 6.42, w: 12.13, h: 0.4, isTextBox: true, margin: 0,
      fontFace: SANS, fontSize: 11, color: INK2 });
  s.addNotes("174 network-free tests cover the scorer, these gates, the sizing maths and the execution path.");

  // ---- 5 AI logic ----------------------------------------------------------
  s = pres.addSlide();
  shell(s, "04  ·  AI logic");
  title(s, "An AI that is structurally incapable of adding risk");
  flow(s, [["SCORED SIGNAL", "VWAP, OR break, RSI,\nEMA, relative volume"],
           ["RULES GATE", "score ≥ 0.70, outside the\nhalf-OR band, regime agrees"],
           ["CLAUDE VETO", "bull / bear debate,\nregime read - no-go only"],
           ["ORDER", "ATM long via the\nAlpaca CLI"]], 2.05, 1.55, 2);
  card(s, { x: 0.6, y: 3.95, w: 5.95, h: 1.85, label: "The one-way rule",
    value: "go → no-go only", valueSize: 22, valueColor: GOLD, valueH: 0.45,
    note: "The model can remove a trade the rules approved. It can never resurrect one they rejected, so the deterministic path is a strict subset of the AI path - and both are auditable against each other.",
    noteSize: 11 });
  card(s, { x: 6.78, y: 3.95, w: 5.95, h: 1.85, label: "Fails closed, never open",
    value: "degrade, don't guess", valueSize: 20, valueColor: INK, valueH: 0.45,
    note: "A missing key or unparseable reply degrades to the deterministic gate and says so in the journal. The agent never trades on a model response it could not read.",
    noteSize: 11 });
  s.addText("The scored sessions ran the deterministic gate; the Claude veto ships tested and runnable via --decision-mode llm.",
    { x: 0.6, y: 6.0, w: 12.13, h: 0.4, isTextBox: true, margin: 0,
      fontFace: MONO, fontSize: 10, color: MUTED });
  s.addNotes("Most submissions say 'LLM proposes, code decides'. This goes further: the model is "
    + "wired so it cannot increase exposure even if it wanted to.");

  // ---- 6 Alpaca infrastructure --------------------------------------------
  s = pres.addSlide();
  shell(s, "05  ·  Alpaca infrastructure");
  title(s, "Built on Alpaca's CLI, not just the SDK");
  [
    ["Orders via the Alpaca CLI", "alpaca order submit, then poll to a terminal state. The agent books the real broker fill, not a quote - so cash P&L is buy-fill against sell-fill."],
    ["Paper endpoint, proven", "Refuses to place anything unless `alpaca doctor` resolves paper-api.alpaca.markets. The env var alone isn't proof - a profile's live_trade can route live."],
    ["Idempotent submits", "On an ambiguous failure it reconciles by --client-order-id via `alpaca order get-by-client-id` before ever resubmitting. No duplicate, no orphan."],
    ["Unattended and auditable", "Market data (5m bars, contract discovery, ATM quotes) via the Alpaca SDK. Isolated Docker container, one session per weekday, JSONL journal per scan."],
  ].forEach(([k, v], i) => {
    card(s, { x: 0.6 + (i % 2) * 6.18, y: 1.95 + Math.floor(i / 2) * 2.15, w: 5.95, h: 1.95,
      label: k, note: v, noteSize: 12 });
  });
  s.addText("Requirement: projects must use Alpaca's MCP server or its CLI tools.   —   every order in the book above went through the CLI.",
    { x: 0.6, y: 6.35, w: 12.13, h: 0.4, isTextBox: true, margin: 0,
      fontFace: MONO, fontSize: 11, color: GOLD });
  s.addNotes("The CLI is not a box-tick. It is why the P&L two slides back is broker truth "
    + "rather than a mid-price model.");

  // ---- 7 the audit trail ---------------------------------------------------
  s = pres.addSlide();
  shell(s, "06  ·  The audit trail");
  title(s, "Most agents show the trades they took");
  s.addText("This one shows the ones it refused.", { x: 0.6, y: 1.72, w: 12.1, h: 0.6,
    isTextBox: true, margin: 0, fontFace: SANS, fontSize: 30, bold: true, color: GOLD });
  s.addShape("roundRect", { x: 0.6, y: 2.65, w: 7.4, h: 3.15, fill: { color: CARD },
    line: { color: LINE, width: 1 }, rectRadius: 0.08 });
  s.addText("logs/decisions-2026-09-03.jsonl", { x: 0.88, y: 2.82, w: 6.9, h: 0.28,
    isTextBox: true, margin: 0, fontFace: MONO, fontSize: 10, color: MUTED, charSpacing: 1.2 });
  s.addText([
    { text: '{ "symbol": "QQQ",', options: { color: INK2, breakLine: true } },
    { text: '  "score": 0.68,', options: { color: INK, breakLine: true } },
    { text: '  "would_have_direction": "put",', options: { color: INK2, breakLine: true } },
    { text: '  "noise_band": { "state": "inside" },', options: { color: INK2, breakLine: true } },
    { text: '  "gate": { "go": false,', options: { color: INK, breakLine: true } },
    { text: '    "rationale": "score 0.68 < min 0.70" },', options: { color: GOLD, breakLine: true } },
    { text: '  "order": null }', options: { color: INK2 } },
  ], { x: 0.88, y: 3.2, w: 6.9, h: 2.4, isTextBox: true, margin: 0,
       fontFace: MONO, fontSize: 13, lineSpacingMultiple: 1.28 });
  card(s, { x: 8.28, y: 2.65, w: 4.45, h: 1.5, label: "One record per scan",
    value: "Full reasoning", valueSize: 20, valueColor: GOLD, valueH: 0.42,
    note: "Score, every signal, band state, gate verdict, risk verdict, order.", noteSize: 11 });
  card(s, { x: 8.28, y: 4.3, w: 4.45, h: 1.5, label: "Why it matters",
    value: "Nothing is implicit", valueSize: 20, valueColor: INK, valueH: 0.42,
    note: "You can reconstruct every decision the agent made, and every one it declined to make.", noteSize: 11 });
  const j = journalStats();
  s.addText(`logs/ in the repo carries ${j.records.toLocaleString("en-US")} journal records across `
    + `${j.days} session ${j.days === 1 ? "file" : "files"} - clone it and check any decision against the record.`,
    { x: 0.6, y: 6.05, w: 12.13, h: 0.4, isTextBox: true, margin: 0,
      fontFace: SANS, fontSize: 13, color: INK2 });
  s.addNotes("This is the differentiator. Show a refusal record on screen - a judge seeing a "
    + "complete decision trail, including the no-trades, is the most persuasive frame in the demo.");

  // ---- 8 close -------------------------------------------------------------
  s = pres.addSlide();
  shell(s, "Every trade provable. Every loss capped.");
  s.addText([
    { text: "A fresh $100,000 account,", options: { color: INK, breakLine: true } },
    { text: `${bk.sessions.length} sessions, ${signed(bk.pct)}.`, options: { color: GOLD } },
  ], { x: 0.6, y: 1.5, w: 12.1, h: 1.9, isTextBox: true, margin: 0,
       fontFace: SANS, fontSize: 46, bold: true, lineSpacingMultiple: 1.05 });
  [
    ["Max loss capped by the instrument", "not by code that has to remember"],
    ["An AI that can only remove risk", "structurally unable to add a trade"],
    ["Every order through the Alpaca CLI", "P&L is the real broker fill, not a quote"],
    ["Every decision journaled", "including the ones it refused to take"],
  ].forEach(([k, v], i) => {
    const y = 3.75 + i * 0.66;
    s.addText("—", { x: 0.6, y, w: 0.35, h: 0.4, isTextBox: true, margin: 0,
      fontFace: SANS, fontSize: 15, color: GOLD });
    s.addText([{ text: k, options: { color: INK, bold: true } },
               { text: "   " + v, options: { color: INK2 } }],
      { x: 1.0, y, w: 11.7, h: 0.4, isTextBox: true, margin: 0, fontFace: SANS, fontSize: 15 });
  });
  s.addText(`Alpaca paper account ${ACCT}  ·  paper trading only. Hypothetical results; not investment advice.`,
    { x: 0.6, y: 6.85, w: 8.5, h: 0.3, isTextBox: true, margin: 0,
      fontFace: MONO, fontSize: 9, color: MUTED });
  s.addNotes("Close on the four claims. Each one is checkable in the repo.");


  // ---- 9 the required one-page write-up ------------------------------------
  // The hackathon accepts this as a slide, a section of the description, or a
  // repo page. It ships as all three; this is the slide form.
  s = pres.addSlide();
  shell(s, "Required one-page write-up");
  title(s, "AI logic  ·  Risk gates  ·  Alpaca infrastructure");
  s.addShape("roundRect", { x: 0.6, y: 1.82, w: 12.13, h: 0.6, fill: { color: CARD },
    line: { color: GOLD, width: 1 }, rectRadius: 0.08 });
  s.addText([
    { text: `Alpaca paper ${ACCT}  ·  single-leg long 0DTE on SPY / QQQ / IWM  ·  `,
      options: { color: INK2 } },
    { text: `$100,000 → ${usd(bk.equity)} (${signed(bk.pct)}) across ${bk.sessions.length} sessions, `
        + `${bk.entries} entries / ${bk.exits} exits`, options: { color: INK, bold: true } },
  ], { x: 0.9, y: 1.82, w: 11.5, h: 0.6, isTextBox: true, margin: 0, valign: "middle",
       fontFace: SANS, fontSize: 12 });

  const colW = 3.87, colGap = 0.26, colY = 2.62, colH = 4.05;
  bullets(s, 0.6, colY, colW, colH, "1 · AI logic", [
    "Scorer reduces Alpaca 5m bars to one number: VWAP position, opening-range break, RSI, EMA stack, relative volume - plus a proposed direction.",
    "Half-opening-range noise band: is the break larger than the symbol's own morning noise?",
    "Leader breadth: 5 of 8 mega-caps above their 20-day average as of the last completed session. Only ever removes the opposed side.",
    "A Claude layer argues bull and bear over the scored facts and returns strict JSON - and can only turn a go into a no-go.",
    "It can never resurrect a rejected trade, so the deterministic path is a strict subset of the AI path.",
    "A missing key or unparseable reply degrades to the deterministic gate and says so in the journal.",
  ]);
  bullets(s, 0.6 + colW + colGap, colY, colW, colH, "2 · Risk gates", [
    "Single-leg long only: max loss is the premium paid, set by the instrument.",
    "One open lot per (symbol, direction); one direction per underlying.",
    "30-minute entry-anchored dedup, so a persistent score cannot stack the same name.",
    "Sizing trimmed to the account's real options buying power - expensive contracts size down, never reject.",
    "No entry after 15:00 ET; hard flatten at 15:50, so a 0DTE never reaches expiry.",
    "Orphan sweep at startup and EOD for positions the agent did not open.",
    "Exits: +40% sells half and trails the runner; -20% half, -40% the rest; 30-min stop if under water.",
    "174 network-free tests cover all of it.",
  ]);
  bullets(s, 0.6 + (colW + colGap) * 2, colY, colW, colH, "3 · Alpaca infrastructure", [
    "Every order through the Alpaca CLI, not the SDK: `alpaca order submit`, then poll to a terminal state.",
    "So the agent books the real broker fill, not a quote - cash P&L is buy-fill against sell-fill.",
    "Refuses to place anything unless `alpaca doctor` resolves paper-api.alpaca.markets.",
    "An ambiguous submit is reconciled by --client-order-id before it will ever resubmit.",
    "Market data - bars, contract discovery, ATM quotes - via the Alpaca Market Data API.",
    "Runs unattended in an isolated Docker container, one session per weekday.",
    "Every scan appends one JSONL record with the full reasoning to logs/.",
  ]);
  s.addNotes("This slide is the hackathon's required one-page write-up. The same content ships "
    + "as docs/ONE_PAGER.md and docs/ONE_PAGER.pdf in the repo.");

  await pres.writeFile({ fileName: OUT });
  console.log(`wrote ${OUT}  —  ${usd(bk.equity)} (${signed(bk.pct)}), `
    + `${bk.sessions.length} sessions, ${bk.entries} entries / ${bk.exits} exits`);
}

main().catch(e => { console.error(e); process.exit(1); });
