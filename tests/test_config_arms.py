"""Lock the arm ablations: each experimental arm must differ from its control in
EXACTLY the one behavioural key it is meant to isolate — nothing else. Deploys are
gated on pytest, so a later edit that accidentally lets two arms drift apart (or
converge) fails CI instead of silently contaminating a live A/B.

  B vs C  -> only score.require_outside_noise_band_fade   (the half-OR band on fades)
  C vs D  -> IDENTICAL behaviourally (2026-09-03: C moved to 1-min entry, matching
             D). Only the /arm label differs. D is now a duplicate of C pending a
             decision on whether to repurpose it.

Comparison ignores every *_comment string (prose is free to differ)."""
import json
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load(name):
    with open(os.path.join(ROOT, name)) as f:
        return json.load(f)


def _flat(obj, prefix=""):
    """Flatten to {path: scalar}, dropping any key named '_comment' or '*_comment'."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "_comment" or k.endswith("_comment"):
                continue
            out.update(_flat(v, f"{prefix}/{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flat(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def _behavioral_diff(a, b):
    fa, fb = _flat(a), _flat(b)
    keys = set(fa) | set(fb)
    return {k: (fa.get(k), fb.get(k)) for k in keys if fa.get(k) != fb.get(k)}


def test_all_arm_configs_are_valid_json():
    for name in ("config.json", "config.armB.json", "config.armC.json", "config.armD.json"):
        cfg = _load(name)
        assert cfg.get("mode") == "dry_run", f"{name}: mode must ship as dry_run"


def test_armC_is_armB_plus_only_the_fade_band():
    diff = _behavioral_diff(_load("config.armB.json"), _load("config.armC.json"))
    assert set(diff) == {"/arm", "/score/require_outside_noise_band_fade"}, diff
    # Arm B carries NO fade override, so it falls back to require_outside_noise_band
    # (True) — band ON for fades. Arm C sets the override False — band OFF for fades.
    assert diff["/score/require_outside_noise_band_fade"] == (None, False)
    assert _load("config.armB.json")["score"].get("require_outside_noise_band") is True


def test_armD_currently_duplicates_armC():
    """C moved to 1-min entry (2026-09-03), so C and D are now behaviourally
    identical — only the /arm label differs. This lock documents the collapse;
    when D is repurposed, replace this with the new one-key ablation."""
    diff = _behavioral_diff(_load("config.armC.json"), _load("config.armD.json"))
    assert set(diff) == {"/arm"}, diff
    assert diff["/arm"] == ("C", "D")


def test_armD_keeps_the_fade_band_off_like_armC():
    """D inherits C's band ablation; the cadence test must not also move the band."""
    d = _load("config.armD.json")
    assert d["score"]["require_outside_noise_band_fade"] is False
    assert d["score"]["require_outside_noise_band"] is True
    assert d["scan"]["scan_step_minutes"] == 1
    assert d["scan"]["manage_step_minutes"] == 1  # exits already 1-min on every arm
