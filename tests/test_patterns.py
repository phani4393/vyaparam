"""
Regression tests for the detector logic in nse_common.py / patterns.py, run
against synthetic OHLC fixtures instead of live data. These are ported from the
US scanner's test_wyckoff_patterns.py -- since the detector math is reused
verbatim, the same fixtures must produce the same results here. Only the import
sources differ (nse_common / patterns instead of wyckoff_common / wyckoff_patterns).

Run with: python -m pytest tests/ -v   (from the repo root)

Fixture note: baseline bars use a negligible monotonic drift (1e-5/bar) rather
than a flat price, so pivots() doesn't flag spurious pivots on tie windows.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import nse_common as c
from nse_common import is_pure_spring, is_pure_upthrust
from patterns import abc_pattern

LEFT, RIGHT = c.LEFT_BARS, c.RIGHT_BARS


def make_flat_bars(n):
    out = []
    for i in range(n):
        price = 100.0 - i * 0.00001
        out.append({"date": f"2024-{i:05d}", "open": price, "high": price + 0.5,
                     "low": price - 0.5, "close": price, "volume": 1000})
    return out


def set_bar(bars, idx, **kwargs):
    bars[idx] = {**bars[idx], **kwargs}


def test_pure_spring_fires_on_undercut_and_recover():
    n = LEFT + RIGHT + 20
    bars = make_flat_bars(n)
    pivot_idx = LEFT
    set_bar(bars, pivot_idx, low=90.0, close=91.0)
    _, sup = c.pivots(bars)
    assert sup[pivot_idx + RIGHT] == 90.0

    spring_idx = pivot_idx + RIGHT + 5
    set_bar(bars, spring_idx, low=88.0, high=92.0, close=91.0, open=90.0)
    _, sup = c.pivots(bars)
    assert is_pure_spring(bars, sup, spring_idx)


def test_pure_spring_does_not_fire_on_genuine_breakdown():
    n = LEFT + RIGHT + 20
    bars = make_flat_bars(n)
    pivot_idx = LEFT
    set_bar(bars, pivot_idx, low=90.0, close=91.0)
    breakdown_idx = pivot_idx + RIGHT + 5
    set_bar(bars, breakdown_idx, low=87.0, high=89.0, close=87.5, open=88.0)
    _, sup = c.pivots(bars)
    assert not is_pure_spring(bars, sup, breakdown_idx)


def test_pure_upthrust_fires_on_poke_and_fail():
    n = LEFT + RIGHT + 30
    bars = make_flat_bars(n)
    pivot_idx = LEFT
    set_bar(bars, pivot_idx, high=110.0, close=109.0)
    res, _ = c.pivots(bars)
    assert res[pivot_idx + RIGHT] == 110.0

    ut_idx = pivot_idx + RIGHT + 5
    set_bar(bars, ut_idx, high=112.0, low=107.0, close=108.0, open=110.0, volume=1500)
    res, _ = c.pivots(bars)
    assert is_pure_upthrust(bars, res, ut_idx)


def test_pure_upthrust_does_not_fire_on_weak_penetration():
    n = LEFT + RIGHT + 30
    bars = make_flat_bars(n)
    pivot_idx = LEFT
    set_bar(bars, pivot_idx, high=110.0, close=109.0)
    ut_idx = pivot_idx + RIGHT + 5
    set_bar(bars, ut_idx, high=111.0, low=107.0, close=108.0, open=110.0, volume=1500)
    res, _ = c.pivots(bars)
    assert not is_pure_upthrust(bars, res, ut_idx)


def test_pure_upthrust_does_not_fire_on_genuine_breakout():
    n = LEFT + RIGHT + 20
    bars = make_flat_bars(n)
    pivot_idx = LEFT
    set_bar(bars, pivot_idx, high=110.0, close=109.0)
    breakout_idx = pivot_idx + RIGHT + 5
    set_bar(bars, breakout_idx, high=112.0, low=110.5, close=111.0, open=110.0)
    res, _ = c.pivots(bars)
    assert not is_pure_upthrust(bars, res, breakout_idx)


def test_no_spurious_signals_on_pure_noise_free_bars():
    bars = make_flat_bars(LEFT + RIGHT + 10)
    res, sup = c.pivots(bars)
    for j in range(len(bars)):
        assert not is_pure_spring(bars, sup, j)
        assert not is_pure_upthrust(bars, res, j)


def _abc_fixture():
    i0, i1, i2, i3 = 20, 50, 80, 110
    n = i3 + RIGHT + 15
    bars = make_flat_bars(n)
    set_bar(bars, i0, low=70.0, close=71.0)
    set_bar(bars, i1, high=130.0, close=129.0)
    set_bar(bars, i2, low=94.0, close=95.0)
    set_bar(bars, i3, high=134.0, close=133.5)
    return bars, i3


def test_abc_does_not_fire_before_point_c_is_confirmable():
    bars, i3 = _abc_fixture()
    for extra in range(0, RIGHT):
        truncated = bars[: i3 + extra + 1]
        assert abc_pattern(truncated) is None


def test_abc_fires_exactly_on_the_confirmation_day_only():
    bars, i3 = _abc_fixture()
    confirm_len = i3 + RIGHT + 1
    r = abc_pattern(bars[:confirm_len])
    assert r is not None and r["isNew"] is True
    assert r["pointC"]["idx"] == i3

    r_next = abc_pattern(bars[: confirm_len + 1])
    assert r_next is not None and r_next["isNew"] is False
