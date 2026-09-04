"""Order execution + exit management for single-leg long 0DTE options.

Orders go through the Alpaca CLI (src/broker_cli.py), not the SDK. Entries are
placed as a MARKETABLE LIMIT (ask + a small buffer) so a multi-contract order
cannot walk a wide 0DTE book; we POLL until terminal, then book the REAL filled
quantity and fill price. Market data (quotes, contracts) still comes from the SDK.

Sizing is ADAPTIVE: the agent asks for up to `contracts_per_trade`, but the size
is trimmed to fit both the per-trade premium budget and the remaining aggregate
buying-power room, so an expensive contract sizes DOWN instead of being rejected.

Partial fills are first-class (they appear once qty > 1):
  * a BUY that fills partially opens the position at the ACTUAL filled qty;
  * a SELL that fills partially decrements the remaining qty and leaves the
    position open to retry — it is never marked closed until qty == 0.

Exits:
  * profit target is a SCALE-OUT — it sells half the opened qty ONCE and leaves a
    runner (a 1-lot has no half, so it closes outright);
  * the runner is trailed by giving back `runner_giveback_pct` of its PEAK GAIN
    (measured on the gain, not the price);
  * premium-stop / time-stop / EOD sell ALL remaining contracts.

Pricing: entry basis = actual buy fill (paper) / modeled ask (dry_run); triggers
read the MID; realized cash P&L = (sell fill - buy fill) * 100 * leg_qty (paper),
or modeled (bid - ask) (dry_run). % is mid-based, $ is fill-vs-fill; both reported.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .broker_cli import BrokerCLI, BrokerError

ET = ZoneInfo("America/New_York")


@dataclass
class Position:
    symbol: str
    direction: str
    contract: str            # OCC option symbol
    strike: float
    expiration: str
    qty: int                 # REMAINING held contracts (shrinks as legs sell)
    entry_ask: float         # quoted ask at decision (reference)
    entry_mid: float         # % basis
    entry_time: datetime     # ET
    cost_usd: float
    order_id: Optional[str] = None
    entry_fill: Optional[float] = None   # actual buy fill (paper); None in dry_run
    open: bool = True
    opened_qty: Optional[int] = None     # contracts the position opened with
    scaled: bool = False                 # has ANY profit-target scale-out fired
    scale_count: int = 0                 # ladder exit: how many scale tiers have fired (0/1/2)
    loss_scale_count: int = 0            # stop-loss ladder: how many loss tiers have fired (0/1)
    high_water_pct: float = 0.0          # runner peak P&L% (mid-based)
    high_water_price: float = 0.0        # ladder exit: peak MID price, for the price-drop trail
    detail: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.opened_qty is None:
            self.opened_qty = self.qty

    @property
    def entry_basis(self) -> float:
        """What you actually paid per contract: fill when we have it, else ask."""
        return self.entry_fill if self.entry_fill is not None else self.entry_ask


@dataclass
class ExitEvent:
    position: Position
    reason: str
    exit_bid: float
    exit_mid: float
    pnl_pct: float           # mid-based
    pnl_usd: float           # cash on THIS leg
    exit_fill: Optional[float] = None    # actual sell fill (paper); None in dry_run
    qty: int = 0             # contracts sold in this leg


class Executor:
    def __init__(self, md, cfg, broker: Optional[BrokerCLI] = None, echo=None):
        self.md = md
        self.cfg = cfg
        self.mode = cfg.mode
        self.exits_cfg = cfg.exits
        self.risk_cfg = cfg.risk
        self.target_qty = int(cfg.risk.get("contracts_per_trade", 1))
        # Aggregate-exposure ceiling used by sizing. Starts from config and is
        # capped by the REAL options buying power below (R10).
        self.max_open_premium = float(cfg.risk.get("max_open_premium_usd", 1e12))
        self.positions: list[Position] = []
        self.trades_today = 0
        # Per-symbol session history the entry rules read. Written ONLY on a
        # confirmed FULL close / confirmed entry, so a failed order never arms a
        # cooldown or burns a direction.
        self.last_exit: dict[str, tuple] = {}          # symbol -> (datetime, reason)
        self.directions_today: dict[str, set] = {}     # symbol -> {"call","put"}
        # Entry-anchored dedup clock (`dedup_minutes`): last CONFIRMED
        # entry time per (symbol, direction). Distinct from last_exit, which is
        # exit-anchored — this one throttles re-entry BEFORE anything closes, so
        # a symbol cannot be stacked every scan while its score stays high.
        self.last_entry: dict[tuple, datetime] = {}    # (symbol, direction) -> datetime
        self._echo = echo or (lambda msg: None)
        self.broker = broker
        if self.mode == "paper" and self.broker is None:
            self.broker = BrokerCLI(echo=lambda c: self._echo(f"cli: {c}"))
            # Prove we're on the paper endpoint before the agent places anything.
            self.broker.verify_paper()
        # R10: cap the aggregate ceiling by the account's REAL options buying
        # power, so sizing never plans past what the account can actually hold.
        if (self.mode == "paper" and self.broker is not None
                and getattr(self.broker, "options_buying_power", None)):
            try:
                bp = self.broker.options_buying_power()
            except BrokerError:
                bp = None
            if bp and bp > 0:
                self.max_open_premium = min(self.max_open_premium, float(bp))
                self._echo(f"options buying power ${bp:,.0f}; aggregate premium "
                           f"cap set to ${self.max_open_premium:,.0f}")
        # Orphan guard: dump any option position we did not open, so we never
        # manage/expire something outside our tracking. (Skipped for an injected
        # broker that doesn't support listing — e.g. unit-test fakes.)
        if (self.mode == "paper" and self.broker is not None
                and bool(cfg.risk.get("flatten_foreign_at_startup", True))
                and getattr(self.broker, "list_option_positions", None)):
            self._flatten_broker_positions("startup (foreign position)")

    @property
    def open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.open]

    def open_premium_usd(self) -> float:
        return float(sum(p.cost_usd for p in self.open_positions))

    # ---------------------------------------------------------------- sizing
    def size_for(self, ask: float, open_premium: float) -> int:
        """Contracts to buy: the target, trimmed to fit the per-trade budget AND
        the remaining aggregate premium room. 0 means 'cannot afford even one'."""
        per_contract = float(ask) * 100.0
        if per_contract <= 0:
            return 0
        budget = float(self.risk_cfg.get("max_premium_per_trade_usd", 1e12))
        room = self.max_open_premium - float(open_premium)
        affordable = min(budget, room)
        if affordable < per_contract:
            return 0
        return max(0, min(self.target_qty, int(affordable // per_contract)))

    # ------------------------------------------------------------------ entry
    def quote_entry(self, symbol: str, direction: str, spot: Optional[float] = None,
                    open_premium: float = 0.0) -> Optional[dict]:
        """Resolve the ATM contract, its quote, and the ADAPTIVE size. None if no
        contract/quote or if not even one contract fits the remaining room."""
        c = self.md.atm_contract(symbol, direction, spot=spot)
        if not c:
            return None
        q = self.md.option_quote(c["symbol"])
        if not q or q["ask"] <= 0:
            return None
        qty = self.size_for(q["ask"], open_premium)
        if qty < 1:
            return None
        cost = q["ask"] * 100 * qty
        return {"contract": c, "quote": q, "cost_usd": round(cost, 2), "qty": qty}

    def _entry_limit(self, ask: float) -> float:
        slip = float(self.risk_cfg.get("entry_limit_slippage_pct", 2.0))
        return round(float(ask) * (1.0 + slip / 100.0), 2)

    def enter(self, symbol: str, direction: str, prepared: dict) -> Position:
        c, q = prepared["contract"], prepared["quote"]
        want = int(prepared.get("qty", self.target_qty))
        order_id = None
        entry_fill = None
        opened = want
        cost_usd = prepared["cost_usd"]
        if self.mode == "paper":
            coid = f"entry-{c['symbol']}-{int(datetime.now(timezone.utc).timestamp())}"
            res = self.broker.submit_and_fill(
                c["symbol"], "buy", want, "buy_to_open",
                client_order_id=coid, limit_price=self._entry_limit(q["ask"]))
            order_id = res["order_id"]
            filled = _as_int(res.get("filled_qty"))
            if filled <= 0 or res.get("fill_price") is None:
                # Nothing was actually bought — surface it, book nothing.
                raise BrokerError(
                    f"BUY {c['symbol']} did not fill (status={res['status']}, "
                    f"filled_qty={filled}); no position opened")
            entry_fill = res["fill_price"]
            opened = filled                       # book the ACTUAL filled quantity
            # R5: a pending_cancel remainder can fill AFTER we booked the partial.
            # Reconcile against the broker's true held qty so all of it is managed.
            if opened < want and getattr(self.broker, "list_option_positions", None):
                try:
                    held = int(self.broker.list_option_positions().get(c["symbol"], opened))
                except BrokerError:
                    held = opened
                if held > opened:
                    self._echo(f"reconciled {c['symbol']}: broker holds {held} > "
                               f"booked {opened}; adopting the remainder")
                    opened = held
            cost_usd = round(entry_fill * 100 * opened, 2)
        pos = Position(
            symbol=symbol, direction=direction, contract=c["symbol"],
            strike=c["strike"], expiration=c["expiration"], qty=opened,
            entry_ask=q["ask"], entry_mid=q["mid"], entry_time=datetime.now(ET),
            cost_usd=cost_usd, order_id=order_id, entry_fill=entry_fill,
            opened_qty=opened,
        )
        self.positions.append(pos)
        self.trades_today += 1
        self.directions_today.setdefault(symbol, set()).add(direction)
        self.last_entry[(symbol, direction)] = pos.entry_time
        return pos

    # ------------------------------------------------- entry-rule accessors
    def mins_since_last_exit(self, symbol: str, now: datetime):
        rec = self.last_exit.get(symbol)
        if not rec:
            return None
        return max(0.0, (now - rec[0]).total_seconds() / 60.0)

    def last_exit_reason(self, symbol: str):
        rec = self.last_exit.get(symbol)
        return rec[1] if rec else None

    def directions_for(self, symbol: str) -> tuple:
        return tuple(sorted(self.directions_today.get(symbol, ())))

    def holds_open(self, symbol: str, direction: str) -> bool:
        """True if an OPEN tracked lot already exists on this symbol+direction.

        The agent carries at most ONE open lot per (symbol, direction) and
        re-enters only after that lot has fully closed. Its in-memory open book
        is a faithful stand-in for the broker's — foreign and manual positions
        are flattened at startup, so what the agent tracks as open IS what the
        account holds. Without this guard the entry-anchored dedup is only a
        30-min throttle: once the window expires, the same still-qualifying
        signal stacks a SECOND lot on a symbol already held (observed
        2026-08-31: IWM at 10:37 and again at 11:07)."""
        return any(p.symbol == symbol and p.direction == direction
                   for p in self.open_positions)

    def mins_since_last_entry(self, symbol: str, direction: str, now: datetime):
        """Minutes since the last CONFIRMED entry on this symbol+direction, or
        None if none yet. Feeds the entry-anchored dedup gate
        (`dedup_minutes`), which stops a symbol being re-entered every scan."""
        ts = self.last_entry.get((symbol, direction))
        if ts is None:
            return None
        return max(0.0, (now - ts).total_seconds() / 60.0)

    # ------------------------------------------------------------------- exits
    def _exit_reason(self, pos: Position, mid: float, now: datetime,
                     force_eod: bool) -> Optional[str]:
        if force_eod:
            return "eod_flatten"
        if pos.entry_mid <= 0:
            return None
        pnl_pct = (mid - pos.entry_mid) / pos.entry_mid * 100
        if pnl_pct >= float(self.exits_cfg.get("profit_target_pct", 40)):
            return "profit_target"
        if pnl_pct <= float(self.exits_cfg.get("premium_stop_pct", -65)):
            return "premium_stop"
        elapsed_min = (now - pos.entry_time).total_seconds() / 60
        if (elapsed_min >= float(self.exits_cfg.get("time_stop_minutes", 30))
                and pnl_pct <= 0):
            return "time_stop"
        return None

    def _pnl_pct(self, pos: Position, mid: float) -> float:
        return ((mid - pos.entry_mid) / pos.entry_mid * 100) if pos.entry_mid else 0.0

    def _stop_ladder_exit(self, pos: Position, q: dict, pnl_pct: float,
                          now: datetime) -> Optional[ExitEvent]:
        """Downside stop-loss ladder shared by BOTH exit engines: sell HALF of
        what remains at stop1_loss_pct (default -20%), then the REST at
        stop2_loss_pct (default -40%). NOTE the stop2 branch tests `<= s2`, so it
        also catches anything below -40%: while the ladder is enabled the -65%
        premium_stop in each engine is unreachable, and only applies when
        stop_ladder_enabled is false. The half-cut deliberately does NOT mark the
        remainder as a runner (see _sell's mark_runner), so the surviving half
        keeps its time stop and profit target.
        Returns an ExitEvent (mutating loss_scale_count) or None. Disabled unless
        exits.stop_ladder_enabled is true."""
        if not bool(self.exits_cfg.get("stop_ladder_enabled", False)):
            return None
        s1 = float(self.exits_cfg.get("stop1_loss_pct", -20))
        s2 = float(self.exits_cfg.get("stop2_loss_pct", -40))
        if pnl_pct <= s2 + 1e-9:                         # deep: close whatever remains
            return self._sell(pos, q, pos.qty, "stop_loss_2", is_scale=False, now=now)
        if pos.loss_scale_count == 0 and pnl_pct <= s1 + 1e-9:  # first cut: half of remaining
            half = max(int(pos.qty) // 2, 1)
            ev = self._sell(pos, q, half, "stop_loss_1", is_scale=True, now=now,
                            mark_runner=False)
            if ev is not None:
                pos.loss_scale_count = 1
            return ev
        return None

    def manage(self, now: datetime, force_eod: bool = False) -> list[ExitEvent]:
        """Dispatch to the configured exit engine. `exit_mode` selects it:
        'scale_single' (the default) or 'ladder'."""
        if str(self.exits_cfg.get("exit_mode", "scale_single")) == "ladder":
            return self._manage_ladder(now, force_eod=force_eod)
        return self._manage_scale_single(now, force_eod=force_eod)

    def _manage_scale_single(self, now: datetime,
                             force_eod: bool = False) -> list[ExitEvent]:
        """scale_single: one profit-target scale-out (half) then a peak-gain-giveback
        runner trail. Each event (including the scale leg) is returned for the log."""
        events: list[ExitEvent] = []
        scale_on = bool(self.exits_cfg.get("scale_out_at_target", False))
        giveback = float(self.exits_cfg.get("runner_giveback_pct", 40))
        for pos in self.open_positions:
            q = self.md.option_quote(pos.contract)
            if not q:
                continue
            pnl_pct = self._pnl_pct(pos, q["mid"])
            if force_eod:
                ev = self._sell(pos, q, pos.qty, "eod_flatten", is_scale=False, now=now)
            elif (sev := self._stop_ladder_exit(pos, q, pnl_pct, now)) is not None:
                ev = sev
            elif not pos.scaled:
                reason = self._exit_reason(pos, q["mid"], now, False)
                if (reason == "profit_target" and scale_on and pos.opened_qty >= 2):
                    half = max(int(pos.opened_qty) // 2, 1)
                    ev = self._sell(pos, q, half, "profit_target", is_scale=True, now=now)
                    if ev is not None:
                        pos.high_water_pct = max(pos.high_water_pct, pnl_pct)
                elif reason:
                    ev = self._sell(pos, q, pos.qty, reason, is_scale=False, now=now)
                else:
                    ev = None
            else:                                   # runner: trail or hard stop
                pos.high_water_pct = max(pos.high_water_pct, pnl_pct)
                floor = pos.high_water_pct * (1.0 - giveback / 100.0)
                if pnl_pct <= float(self.exits_cfg.get("premium_stop_pct", -65)):
                    ev = self._sell(pos, q, pos.qty, "premium_stop", is_scale=False, now=now)
                elif pos.high_water_pct > 0 and pnl_pct <= floor:
                    ev = self._sell(pos, q, pos.qty, "runner_trail", is_scale=False, now=now)
                else:
                    ev = None
            if ev is not None:
                events.append(ev)
        return events

    def _manage_ladder(self, now: datetime,
                       force_eod: bool = False) -> list[ExitEvent]:
        """ladder: two-tier scale-out ladder.

          tier 1  at +tier1_target_pct (default 20%): sell HALF the opened qty.
          after tier 1, the runner is trailed by tier1_trail_price_drop_pct
                  (default 10%) off its PEAK MID PRICE — sell it all if the mid
                  falls that far from its high-water price.
          tier 2  if it instead reaches +tier2_target_pct (default 40%): sell
                  HALF of what remains, then the leftover rides the SAME
                  peak-gain-giveback trail scale_single uses (runner_giveback_pct).
          premium_stop / time_stop apply as in scale_single (time_stop only before
          tier 1, since after +20% the position is in profit).

        Everything is priced off the option MID from a fresh Alpaca quote, so this
        can run on a fast (1-min) cadence without touching yfinance."""
        events: list[ExitEvent] = []
        t1 = float(self.exits_cfg.get("tier1_target_pct", 20))
        t2 = float(self.exits_cfg.get("tier2_target_pct", 40))
        drop = float(self.exits_cfg.get("tier1_trail_price_drop_pct", 10))
        giveback = float(self.exits_cfg.get("runner_giveback_pct", 40))
        pstop = float(self.exits_cfg.get("premium_stop_pct", -65))
        tstop = float(self.exits_cfg.get("time_stop_minutes", 30))
        for pos in self.open_positions:
            q = self.md.option_quote(pos.contract)
            if not q:
                continue
            mid = q["mid"]
            pnl_pct = self._pnl_pct(pos, mid)
            # High-water marks updated every tick, before any decision, so both
            # trails measure from the true peak regardless of stage.
            pos.high_water_pct = max(pos.high_water_pct, pnl_pct)
            pos.high_water_price = max(pos.high_water_price, mid)
            ev = None
            if force_eod:
                ev = self._sell(pos, q, pos.qty, "eod_flatten", is_scale=False, now=now)
            elif (sev := self._stop_ladder_exit(pos, q, pnl_pct, now)) is not None:
                ev = sev
            elif pos.scale_count == 0:
                elapsed = (now - pos.entry_time).total_seconds() / 60
                if pnl_pct <= pstop:
                    ev = self._sell(pos, q, pos.qty, "premium_stop", is_scale=False, now=now)
                elif elapsed >= tstop and pnl_pct <= 0:
                    ev = self._sell(pos, q, pos.qty, "time_stop", is_scale=False, now=now)
                elif pnl_pct >= t1 - 1e-9:             # eps: +20.0% exactly should fire
                    if pos.opened_qty >= 2:
                        half = max(int(pos.opened_qty) // 2, 1)
                        ev = self._sell(pos, q, half, "profit_target", is_scale=True, now=now)
                        if ev is not None:
                            pos.scale_count = 1
                    else:                              # 1-lot: no half, close outright
                        ev = self._sell(pos, q, pos.qty, "profit_target", is_scale=False, now=now)
            elif pos.scale_count == 1:
                trail_hit = (pos.high_water_price > 0
                             and mid <= pos.high_water_price * (1.0 - drop / 100.0))
                if pnl_pct <= pstop:
                    ev = self._sell(pos, q, pos.qty, "premium_stop", is_scale=False, now=now)
                elif pnl_pct >= t2 - 1e-9 and pos.qty >= 2:
                    half = max(int(pos.qty) // 2, 1)
                    ev = self._sell(pos, q, half, "profit_target_2", is_scale=True, now=now)
                    if ev is not None:
                        pos.scale_count = 2
                elif trail_hit:
                    ev = self._sell(pos, q, pos.qty, "runner_trail", is_scale=False, now=now)
            else:                                      # scale_count >= 2: gain-giveback trail
                floor = pos.high_water_pct * (1.0 - giveback / 100.0)
                if pnl_pct <= pstop:
                    ev = self._sell(pos, q, pos.qty, "premium_stop", is_scale=False, now=now)
                elif pos.high_water_pct > 0 and pnl_pct <= floor:
                    ev = self._sell(pos, q, pos.qty, "runner_trail", is_scale=False, now=now)
            if ev is not None:
                events.append(ev)
        return events

    def _close(self, pos: Position, q: dict, reason: str,
               now: Optional[datetime] = None) -> Optional[ExitEvent]:
        """Full close of the remaining quantity (kept as the simple entrypoint)."""
        return self._sell(pos, q, pos.qty, reason, is_scale=False, now=now)

    def _sell(self, pos: Position, q: dict, want_qty: int, reason: str,
              is_scale: bool, now: Optional[datetime] = None,
              mark_runner: bool = True) -> Optional[ExitEvent]:
        """Sell up to `want_qty` contracts. Decrements remaining; only a fully
        closed position (qty == 0) is marked closed and arms the cooldown. A
        scale-out leaves the runner open and never arms the cooldown.

        `mark_runner` exists for the LOSS side. Setting pos.scaled routes the
        remainder into the runner branch, which trails a high-water mark and
        skips _exit_reason entirely — correct after a +40% profit scale-out,
        wrong after a -20% loss cut, because it would leave the surviving half
        with no time stop and no profit target until -40% or the EOD flatten.
        The loss ladder therefore scales without claiming the position is a
        runner; pos.loss_scale_count is what stops it re-firing."""
        want = min(int(want_qty), pos.qty)
        if want <= 0:
            return None
        now = now or datetime.now(ET)
        exit_fill = None
        filled = want
        if self.mode == "paper":
            coid = f"exit-{pos.contract}-{int(datetime.now(timezone.utc).timestamp())}"
            try:
                res = self.broker.submit_and_fill(pos.contract, "sell", want,
                                                  "sell_to_close", client_order_id=coid)
            except BrokerError as exc:
                self._echo(f"sell FAILED for {pos.contract}: {exc}; will retry next scan")
                return None
            filled = _as_int(res.get("filled_qty"))
            if filled <= 0 or res.get("fill_price") is None:
                self._echo(f"sell {pos.contract} not filled (status={res['status']}); "
                           "position stays open for the next scan")
                return None
            filled = min(filled, want)
            exit_fill = res["fill_price"]
        pos.qty -= filled
        # Keep cost_usd = the remaining held cost, so open_premium_usd() (and thus
        # the aggregate BP guard) reflects only what is still held after a leg sells.
        pos.cost_usd = round(pos.entry_basis * 100 * max(pos.qty, 0), 2)
        if is_scale and mark_runner:
            pos.scaled = True                       # a leg went; remainder is the runner
        fully_closed = pos.qty <= 0
        if fully_closed:
            pos.open = False
            # Arm the per-symbol cooldown only on a CONFIRMED full close.
            self.last_exit[pos.symbol] = (now, reason)
        pnl_pct = self._pnl_pct(pos, q["mid"])
        if exit_fill is not None:                   # paper: real fill-vs-fill cash
            pnl_usd = (exit_fill - pos.entry_basis) * 100 * filled
        else:                                       # dry_run: modeled bid-vs-ask
            pnl_usd = (q["bid"] - pos.entry_ask) * 100 * filled
        return ExitEvent(position=pos, reason=reason, exit_bid=q["bid"],
                         exit_mid=q["mid"], pnl_pct=round(pnl_pct, 1),
                         pnl_usd=round(pnl_usd, 2), exit_fill=exit_fill, qty=filled)

    # -------------------------------------------------------- broker reconcile
    def flatten_broker_stragglers(self) -> list[dict]:
        """Sell any option position still held at the broker (EOD safety net for
        orphans). Returns a list of what it flattened. No-op in dry_run."""
        return self._flatten_broker_positions("eod (broker straggler)")

    def _flatten_broker_positions(self, context: str) -> list[dict]:
        flattened: list[dict] = []
        if self.mode != "paper" or self.broker is None:
            return flattened
        if not getattr(self.broker, "list_option_positions", None):
            return flattened
        try:
            held = self.broker.list_option_positions()
        except BrokerError as exc:
            self._echo(f"could not list broker positions for {context}: {exc}")
            return flattened
        for occ, qty in held.items():
            if int(qty) <= 0:                       # only long single-leg here
                continue
            coid = f"sweep-{occ}-{int(datetime.now(timezone.utc).timestamp())}"
            try:
                res = self.broker.submit_and_fill(occ, "sell", int(qty),
                                                  "sell_to_close", client_order_id=coid)
                self._echo(f"{context}: flattened {qty}x {occ} "
                           f"(status={res.get('status')}, filled={res.get('filled_qty')})")
                flattened.append({"occ": occ, "qty": int(qty), "status": res.get("status")})
            except BrokerError as exc:
                self._echo(f"{context}: FAILED to flatten {qty}x {occ}: {exc}")
        return flattened


def _as_int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0
