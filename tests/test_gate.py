"""Gate tests: the rules stack, and that the LLM can only veto, never add."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.gate import RulesGate, LLMGate, Decision

SCORE_CFG = {"min_score": 0.70, "require_outside_noise_band": True}


def _scored(score, band_state, direction="call"):
    return {"score": score, "would_have_direction": direction,
            "noise_band": {"state": band_state}}


def test_rules_below_min_no_go():
    d = RulesGate(SCORE_CFG).decide(_scored(0.60, "above"))
    assert not d.go


def test_rules_inside_band_no_go():
    d = RulesGate(SCORE_CFG).decide(_scored(0.85, "inside"))
    assert not d.go and "inside" in d.rationale


def test_rules_band_none_abstains():
    d = RulesGate(SCORE_CFG).decide(_scored(0.85, None))
    assert not d.go and "abstain" in d.rationale


def test_rules_go():
    d = RulesGate(SCORE_CFG).decide(_scored(0.85, "above"))
    assert d.go and d.direction == "call"


def test_rules_no_band_gate_when_disabled():
    cfg = {"min_score": 0.70, "require_outside_noise_band": False}
    d = RulesGate(cfg).decide(_scored(0.75, "inside"))
    assert d.go


# ---- per-mode band flag (drop the band on fades only) ----------------------
def _scored_mode(score, band_state, mode, direction="call"):
    return {"score": score, "would_have_direction": direction,
            "entry_mode": mode, "noise_band": {"state": band_state}}


def test_fade_band_flag_off_admits_inside_fade():
    # band on for momentum, OFF for fades -> an 'inside' FADE goes through
    cfg = {"min_score": 0.70, "require_outside_noise_band": True,
           "require_outside_noise_band_fade": False}
    d = RulesGate(cfg).decide(_scored_mode(0.75, "inside", "fade"))
    assert d.go


def test_fade_band_flag_off_still_gates_momentum():
    # same config: a MOMENTUM entry inside the band is STILL blocked
    cfg = {"min_score": 0.70, "require_outside_noise_band": True,
           "require_outside_noise_band_fade": False}
    d = RulesGate(cfg).decide(_scored_mode(0.75, "inside", "momentum"))
    assert not d.go and "inside" in d.rationale


def test_no_fade_flag_falls_back_to_base_gate():
    # no _fade key: a fade inside the band is blocked, exactly as before
    cfg = {"min_score": 0.70, "require_outside_noise_band": True}
    d = RulesGate(cfg).decide(_scored_mode(0.75, "inside", "fade"))
    assert not d.go and "inside" in d.rationale


class _StubAdvisor:
    def __init__(self, verdict, reason=None):
        self._v = verdict
        self.reason_unavailable = reason

    def debate(self, scored):
        return self._v


def test_llm_cannot_resurrect_rejected():
    gate = LLMGate(RulesGate(SCORE_CFG), _StubAdvisor({"go": True}))
    d = gate.decide(_scored(0.60, "above"))       # rules already reject
    assert not d.go and d.source == "rules_only"


def test_llm_veto():
    gate = LLMGate(RulesGate(SCORE_CFG),
                   _StubAdvisor({"go": False, "rationale": "choppy"}))
    d = gate.decide(_scored(0.85, "above"))
    assert not d.go and d.source == "llm"


def test_llm_confirm():
    gate = LLMGate(RulesGate(SCORE_CFG),
                   _StubAdvisor({"go": True, "rationale": "clean trend"}))
    d = gate.decide(_scored(0.85, "above"))
    assert d.go and d.source == "llm" and d.direction == "call"


def test_llm_unavailable_degrades_to_rules():
    gate = LLMGate(RulesGate(SCORE_CFG), _StubAdvisor(None, reason="no key"))
    d = gate.decide(_scored(0.85, "above"))
    assert d.go and "degraded" in d.source


# ---------------------------------------------------------------------------
# Leader-regime ("T6") filter.
# ---------------------------------------------------------------------------
from src.gate import RulesGate as _RG

_SC = {"min_score": 0.70, "require_outside_noise_band": True}
_ON = {"require_leader_confirmation": True}
_BULL = {"state": "bull", "above": 6, "counted": 8}
_BEAR = {"state": "bear", "above": 2, "counted": 8}


def _t6_scored(direction="call", score=0.80, band="outside"):
    return {"score": score, "would_have_direction": direction,
            "noise_band": {"state": band}}


def test_regime_blocks_only_the_opposed_side():
    g = _RG(_SC, _ON)
    assert g.decide(_t6_scored("call"), regime=_BULL).go
    assert not g.decide(_t6_scored("put"), regime=_BULL).go
    assert g.decide(_t6_scored("put"), regime=_BEAR).go
    assert not g.decide(_t6_scored("call"), regime=_BEAR).go


def test_regime_never_flips_a_direction():
    """A blocked trade is a no-go, never the same trade with the other side."""
    d = _RG(_SC, _ON).decide(_t6_scored("put"), regime=_BULL)
    assert d.go is False and d.direction is None


def test_regime_cannot_rescue_a_trade_the_core_rejected():
    g = _RG(_SC, _ON)
    assert not g.decide(_t6_scored("call", score=0.40), regime=_BULL).go   # score fails
    assert not g.decide(_t6_scored("call", band="inside"), regime=_BULL).go  # band fails


def test_regime_abstains_when_unavailable_by_default():
    g = _RG(_SC, _ON)
    d = g.decide(_t6_scored("call"), regime={"state": None, "counted": 2})
    assert not d.go and "abstain" in d.rationale
    assert not g.decide(_t6_scored("call"), regime=None).go


def test_regime_can_degrade_open_when_configured():
    g = _RG(_SC, {**_ON, "on_unavailable": "allow"})
    assert g.decide(_t6_scored("call"), regime={"state": None, "counted": 2}).go


def test_regime_filter_is_inert_when_disabled():
    g = _RG(_SC, {})
    assert g.decide(_t6_scored("put"), regime=_BULL).go
    assert g.decide(_t6_scored("call"), regime=_BEAR).go


def test_regime_absent_argument_keeps_old_behaviour():
    """Callers that never pass a regime must be unaffected when the filter is off."""
    assert _RG(_SC, {}).decide(_t6_scored("call")).go
