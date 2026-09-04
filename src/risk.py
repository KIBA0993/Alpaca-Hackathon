"""Risk checks + per-symbol entry rules.

Single-leg LONG options => max loss per trade is the premium paid, so risk is
DEFINED by construction. These checks bound how much premium can be at risk at
once and keep entries inside the safe part of the session.

Three per-symbol entry rules also live here, because they are "may I enter"
decisions driven by session state rather than by the signal:

  already-holding guard
      Never open a second lot on a symbol+direction already held; re-enter only
      after that lot has fully closed. Unconditional — a correctness guard, not
      a tunable. It is what the entry dedup below CANNOT provide: dedup is a
      30-min throttle, so once the window expires a still-qualifying signal
      would otherwise stack a second lot on a symbol already held (observed
      2026-08-31: IWM at 10:37 and again at 11:07).

  one_direction_per_underlying
      Once a symbol has traded a direction today, the opposite direction is
      refused for the rest of the session. It keeps the agent from paying the
      spread twice to flip its own position every time an intraday signal
      reverses.

  entry cooldown, optionally SCOPED to the prior exit reason
      After a close on a symbol, refuse re-entry on that symbol for N minutes.
      With `after_exit_reasons` empty the rule is BLANKET; with ["time_stop"] it
      fires only after a time-stop exit, which is how it ships: do not
      immediately re-buy what just timed out on you. Leaving the list
      configurable keeps the blanket rule one config edit away.

Pure logic (state passed in) so it is unit-testable with no account or clock.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import time
from typing import Optional


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass
class RiskState:
    now: time                 # current ET wall-clock
    open_positions: int       # currently open agent positions
    trades_today: int         # entries already taken this session
    premium_cost_usd: float   # cost of THIS proposed entry (mid * 100 * qty)
    # --- per-symbol entry-rule state. All optional, so a caller that does not
    # --- supply them simply gets the old global-only behaviour.
    symbol: Optional[str] = None
    direction: Optional[str] = None            # the direction being proposed now
    mins_since_last_exit: Optional[float] = None   # on THIS symbol; None = none yet
    last_exit_reason: Optional[str] = None         # reason of that most recent exit
    directions_today: tuple = ()               # directions already traded on THIS symbol
    mins_since_last_entry: Optional[float] = None  # on THIS symbol+direction; None = none yet
    already_holding: bool = False              # an OPEN lot already exists on THIS symbol+direction


class RiskManager:
    def __init__(self, risk_cfg: dict, entry_cfg: Optional[dict] = None):
        self.cfg = risk_cfg
        self.entry = entry_cfg or {}
        self.no_entry_after = _parse_hhmm(risk_cfg.get("no_entry_after", "15:00"))
        self.eod_flatten = _parse_hhmm(risk_cfg.get("eod_flatten", "15:50"))

    # ---------------------------------------------------------- entry rules
    @property
    def one_direction_per_underlying(self) -> bool:
        return bool(self.entry.get("one_direction_per_underlying", False))

    @property
    def dedup_minutes(self) -> float:
        """Entry-anchored throttle (`dedup_minutes`, default 30). After
        an entry on a symbol+direction, refuse another on that same pair for N
        minutes — regardless of whether anything has closed. This is what stops a
        symbol being stacked every scan while its score stays above the gate; the
        exit-anchored cooldown below cannot, because it only arms after a close."""
        return float(self.entry.get("dedup_minutes", 0) or 0)

    @property
    def cooldown_minutes(self) -> float:
        return float(self.entry.get("entry_cooldown_minutes", 0) or 0)

    @property
    def cooldown_after_exit_reasons(self) -> tuple:
        """Exit reasons that ARM the cooldown. Empty/absent => blanket cooldown."""
        v = self.entry.get("entry_cooldown_after_exit_reasons") or []
        return tuple(str(x) for x in v)

    def check_entry_rules(self, st: RiskState) -> tuple[bool, str]:
        """Per-symbol gates: already-holding, one-direction, entry dedup, then the
        (optionally scoped) exit cooldown."""
        # Already-holding guard: never stack a
        # second open lot on a symbol+direction we already hold. Unconditional, as
        # — it is a correctness guard, not a tunable. It catches exactly
        # what the dedup throttle cannot: a lot still open once the 30-min window
        # has expired. Reported first, so a held-and-throttled entry reads as
        # "already holding" rather than as a stale timer.
        if st.already_holding:
            return False, (f"already holding an open {st.direction} lot on "
                           f"{st.symbol}; refused (one open lot per "
                           f"symbol+direction)")
        if (self.one_direction_per_underlying and st.direction
                and st.directions_today
                and st.direction not in tuple(st.directions_today)):
            already = "/".join(sorted(set(st.directions_today)))
            return False, (f"{st.symbol} already traded {already} today; "
                           f"{st.direction} refused (one_direction_per_underlying)")
        # Entry-anchored dedup: block a repeat entry on the same symbol+direction
        # within `dedup_minutes` of the last one, so a persistent high score does
        # not stack the same trade every scan.
        ddm = self.dedup_minutes
        if (ddm > 0 and st.mins_since_last_entry is not None
                and st.mins_since_last_entry < ddm):
            return False, (f"dedup: {st.mins_since_last_entry:.0f}m since last "
                           f"{st.direction} entry on {st.symbol} < {ddm:.0f}m")
        mins = self.cooldown_minutes
        if mins > 0 and st.mins_since_last_exit is not None:
            armed = self.cooldown_after_exit_reasons
            # empty list => blanket: every exit arms the clock
            if (not armed) or (st.last_exit_reason in armed):
                if st.mins_since_last_exit < mins:
                    scope = ("blanket" if not armed
                             else f"after {st.last_exit_reason}")
                    return False, (f"cooldown ({scope}): "
                                   f"{st.mins_since_last_exit:.0f}m since last exit on "
                                   f"{st.symbol} < {mins:.0f}m")
        return True, "ok"

    @property
    def contracts_per_trade(self) -> int:
        return int(self.cfg.get("contracts_per_trade", 1))

    def check_entry(self, st: RiskState) -> tuple[bool, str]:
        ok, why = self.check_entry_rules(st)
        if not ok:
            return ok, why
        if st.now >= self.no_entry_after:
            return False, f"past no-entry cutoff {self.no_entry_after.strftime('%H:%M')}"
        cap = int(self.cfg.get("max_concurrent_positions", 3))
        if st.open_positions >= cap:
            return False, f"at max concurrent positions ({cap})"
        # 0 / absent => no daily entry-count cap: turnover is throttled by
        # max_alerts_per_run + dedup + the already-holding guard instead.
        dmax = int(self.cfg.get("max_trades_per_day", 0) or 0)
        if dmax > 0 and st.trades_today >= dmax:
            return False, f"at max trades per day ({dmax})"
        pcap = float(self.cfg.get("max_premium_per_trade_usd", 200))
        if st.premium_cost_usd > pcap:
            return False, (f"premium ${st.premium_cost_usd:.0f} exceeds per-trade "
                           f"cap ${pcap:.0f}")
        return True, "ok"

    def should_flatten(self, now: time) -> bool:
        return now >= self.eod_flatten
