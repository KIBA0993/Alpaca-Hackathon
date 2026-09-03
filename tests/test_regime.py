"""Leader-breadth regime ("T6") — the math and the no-lookahead contract."""
import pytest
from src.regime import leader_breadth, leader_regime, aligned_direction, DEFAULT_LEADERS

RISING = [10.0] * 19 + [99.0]     # last close far above its own SMA-20
FALLING = [99.0] * 19 + [10.0]    # last close far below


def basket(n_up, n_down):
    d = {f"U{i}": list(RISING) for i in range(n_up)}
    d.update({f"D{i}": list(FALLING) for i in range(n_down)})
    return d


def test_counts_above_correctly():
    b = leader_breadth(basket(5, 3))
    assert b["above"] == 5 and b["counted"] == 8 and b["usable"]


def test_threshold_is_a_single_split_at_five():
    assert leader_regime(basket(5, 3))["state"] == "bull"
    assert leader_regime(basket(4, 4))["state"] == "bear"   # 4 is bear, not neutral


def test_no_neutral_zone_exists():
    """The tested rule was one split; a middle band would be a different rule."""
    states = {leader_regime(basket(k, 8 - k))["state"] for k in range(9)}
    assert states == {"bull", "bear"}


def test_abstains_when_too_few_leaders_have_history():
    r = leader_regime({"A": RISING, "B": RISING})      # only 2 usable
    assert r["state"] is None and not r["usable"]
    assert aligned_direction(r["state"]) is None


def test_short_history_symbol_is_skipped_not_counted_as_below():
    """A symbol with 19 bars must not silently count as 'below' its SMA-20."""
    d = basket(5, 3)
    d["SHORT"] = RISING[:19]
    b = leader_breadth(d)
    assert b["counted"] == 8 and b["above"] == 5     # SHORT excluded entirely
    assert b["detail"]["SHORT"] is None


def test_sma_window_includes_the_compared_bar():
    """Convention must match the research harness: mean(closes[-20:]) vs closes[-1]."""
    closes = [float(i) for i in range(1, 21)]         # 1..20, last=20, sma=10.5
    d = {f"S{i}": list(closes) for i in range(6)}
    assert leader_breadth(d)["above"] == 6
    flat = [5.0] * 20                                 # last == sma exactly
    d2 = {f"S{i}": list(flat) for i in range(6)}
    assert leader_breadth(d2)["above"] == 0           # strict >, ties are NOT above


def test_aligned_direction_mapping():
    assert aligned_direction("bull") == "call"
    assert aligned_direction("bear") == "put"
    assert aligned_direction(None) is None


def test_default_basket_is_eight_names():
    assert len(DEFAULT_LEADERS) == 8 and len(set(DEFAULT_LEADERS)) == 8
