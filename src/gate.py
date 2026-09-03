"""The decision gate — the one place the two agent flavours differ.

    rules_only : score >= min AND price outside the half-OR noise band AND (optional)
                 the direction agrees with the leader-breadth regime ("T6")
    llm        : the same rules floor, then an LLM bull/bear + regime veto on top

The gate returns a Decision. Downstream (risk, execution) does not know or care
which gate produced it. Swapping the two is a single config flag — see
config.json "decision_mode". The LLM can only turn a go into a no-go; it can
never turn a no-go into a go, so rules_only is a strict floor under llm.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Decision:
    go: bool
    direction: Optional[str]
    source: str                       # "rules_only" | "llm" | "llm->rules(degraded)"
    rationale: str
    detail: dict = field(default_factory=dict)


class RulesGate:
    """Score threshold + half-OR noise band + optional leader-regime agreement.

    The leader filter ("T6") only ever REMOVES the opposed side: in a bullish
    regime puts are refused, in a bearish one calls are. It never flips a
    direction and never creates a trade. See src/regime.py for its honest
    statistical status — it is not an established edge.
    """

    def __init__(self, score_cfg: dict, regime_cfg: Optional[dict] = None):
        self.cfg = score_cfg
        self.regime_cfg = regime_cfg or {}

    def _regime_block(self, direction, regime) -> Optional[str]:
        """Reason to refuse `direction` under `regime`, or None to allow."""
        if not self.regime_cfg.get("require_leader_confirmation", False):
            return None
        state = (regime or {}).get("state")
        if state is None:
            if str(self.regime_cfg.get("on_unavailable", "abstain")) == "allow":
                return None
            return ("leader regime unavailable "
                    f"({(regime or {}).get('counted', 0)} leaders with history) — abstain")
        want = "call" if state == "bull" else "put"
        if direction != want:
            return (f"leader regime is {state} "
                    f"({(regime or {}).get('above')}/{(regime or {}).get('counted')} "
                    f"above 20-DMA); {direction} is the opposed side")
        return None

    def decide(self, scored: dict, regime: Optional[dict] = None) -> Decision:
        score = float(scored.get("score", 0.0))
        min_score = float(self.cfg.get("min_score", 0.70))
        direction = scored.get("would_have_direction")
        if score < min_score:
            return Decision(False, None, "rules_only",
                            f"score {score:.2f} < min {min_score:.2f}")
        # The half-OR band can be required per ENTRY MODE. A momentum ("trend")
        # entry always honours require_outside_noise_band. A fade ("chop") entry
        # honours require_outside_noise_band_fade IF that key is present, else it
        # falls back to require_outside_noise_band. Absent the fade key, behaviour
        # is unchanged (band applies to both modes) — arm A/B keep the band; arm C
        # sets require_outside_noise_band_fade=false to drop it on fades only.
        band_required = bool(self.cfg.get("require_outside_noise_band", True))
        if scored.get("entry_mode") == "fade" and "require_outside_noise_band_fade" in self.cfg:
            band_required = bool(self.cfg.get("require_outside_noise_band_fade"))
        if band_required:
            state = (scored.get("noise_band") or {}).get("state")
            if state is None:
                return Decision(False, None, "rules_only",
                                "noise band unavailable — abstain (band-gated arm)")
            if state == "inside":
                return Decision(False, None, "rules_only",
                                "price inside the noise band — no confirmed break")
        blocked = self._regime_block(direction, regime)
        if blocked:
            return Decision(False, None, "rules_only", blocked,
                            detail={"regime": regime})
        return Decision(True, direction, "rules_only",
                        f"score {score:.2f} >= {min_score:.2f}, "
                        f"band {(scored.get('noise_band') or {}).get('state')}"
                        + (f", leaders {regime.get('above')}/{regime.get('counted')} "
                           f"({regime.get('state')})" if regime and regime.get("state") else ""),
                        detail={"regime": regime})


class LLMGate:
    """Rules floor, then an LLM veto. Never adds a trade the rules rejected."""

    def __init__(self, rules: RulesGate, advisor):
        self.rules = rules
        self.advisor = advisor

    def decide(self, scored: dict, regime: Optional[dict] = None) -> Decision:
        base = self.rules.decide(scored, regime=regime)
        if not base.go:
            return base                       # LLM cannot resurrect a rejected trade
        verdict = self.advisor.debate(scored) if self.advisor else None
        if verdict is None:
            reason = getattr(self.advisor, "reason_unavailable", None) or "advisor returned nothing"
            base.source = "llm->rules(degraded)"
            base.rationale += f" | LLM unavailable ({reason}) — degraded to rules"
            return base
        if not verdict.get("go", False):
            return Decision(False, None, "llm",
                            f"LLM veto: {verdict.get('rationale', 'unfavourable regime')}",
                            detail=verdict)
        return Decision(True, base.direction, "llm",
                        f"rules go + LLM confirm: {verdict.get('rationale', '')}",
                        detail=verdict)


def make_gate(cfg, advisor=None):
    rules = RulesGate(cfg.score, getattr(cfg, "regime", None))
    if cfg.decision_mode == "llm":
        return LLMGate(rules, advisor)
    return rules
