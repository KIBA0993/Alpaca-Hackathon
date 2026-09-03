"""Optional LLM advisor: a bull/bear debate + regime read over a scored signal.

Used only when decision_mode = llm. It is deliberately ADVISORY and one-sided:
it can veto a trade the rules already approved, never create one. That mirrors
the honest finding — the entry signal has no measured directional edge, so the
LLM's job is risk-off judgement (sit out a bad regime), not alpha generation.

If anthropic is not installed or ANTHROPIC_API_KEY is missing, the advisor is
simply unavailable and the gate degrades to rules_only (logged, not silent).
"""
from __future__ import annotations
import json
from typing import Optional


class LLMAdvisor:
    def __init__(self, api_key: Optional[str], model: str = "claude-sonnet-5",
                 max_tokens: int = 1024):
        self.model = model
        self.max_tokens = max_tokens
        self._client = None
        self.reason_unavailable: Optional[str] = None
        if not api_key:
            self.reason_unavailable = "ANTHROPIC_API_KEY not set"
            return
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)
        except Exception as exc:  # pragma: no cover - import/env dependent
            self.reason_unavailable = f"anthropic client unavailable: {exc}"

    @property
    def available(self) -> bool:
        return self._client is not None

    def debate(self, scored: dict) -> Optional[dict]:
        """Return {'go','confidence','bull','bear','regime','rationale'} or None.

        None means the advisor could not answer (caller degrades to rules_only).
        """
        if not self.available:
            return None
        prompt = self._build_prompt(scored)
        try:
            msg = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            return self._parse(text)
        except Exception:
            return None

    @staticmethod
    def _build_prompt(scored: dict) -> str:
        band = scored.get("noise_band", {})
        facts = {
            "symbol": scored.get("symbol"),
            "proposed_direction": scored.get("would_have_direction"),
            "score": scored.get("score"),
            "signals": scored.get("key_signals"),
            "rsi": scored.get("patterns", {}).get("rsi"),
            "relative_volume": scored.get("relative_volume"),
            "underlying_price": scored.get("underlying_price"),
            "vwap": scored.get("vwap"),
            "opening_range": [scored.get("or_low"), scored.get("or_high")],
            "noise_band_state": band.get("state"),
            "noise_band": {k: band.get(k) for k in ("upper", "lower", "width")},
        }
        return (
            "A rules-based 0DTE scanner has APPROVED this intraday options entry. "
            "Your only job is a risk-off sanity check: is the current regime one to "
            "sit out? You cannot change the direction or force a trade — you can only "
            "confirm (go) or veto (no-go).\n\n"
            f"SIGNAL FACTS:\n{json.dumps(facts, indent=2)}\n\n"
            "Argue the bull case and the bear case for taking this specific 0DTE long "
            "in one or two sentences each, read the regime (trending / choppy / "
            "reversing), then decide. Bias toward VETO only when the regime clearly "
            "undermines the setup (e.g. price pinned mid-range, conflicting signals, "
            "obvious chop). Respond with ONLY this JSON:\n"
            '{"bull": "...", "bear": "...", "regime": "trending|choppy|reversing", '
            '"go": true|false, "confidence": 0.0-1.0, "rationale": "one sentence"}'
        )

    @staticmethod
    def _parse(text: str) -> Optional[dict]:
        text = text.strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            d = json.loads(text[start:end + 1])
        except Exception:
            return None
        if "go" not in d:
            return None
        d["go"] = bool(d["go"])
        return d


_SYSTEM = (
    "You are a disciplined risk manager on a 0DTE options desk. You are skeptical "
    "of intraday setups because you know most carry no real directional edge. You "
    "veto when the regime is unfavourable and otherwise let approved trades pass. "
    "You never invent conviction you don't have. Output strict JSON only."
)
