from __future__ import annotations

import pytest

from orbcalc.eras import EraSet


def test_windows_are_generated_in_order_for_each_era():
    eras = EraSet([[0, 10], [20, 25]])

    assert eras.windows(4) == [0.0, 4.0, 8.0, 20.0, 24.0]


def test_intersect_returns_each_overlapping_segment_in_input_order():
    eras = EraSet([[0, 10], [20, 30], [40, 50]])

    assert eras.intersect(8, 42) == [[8.0, 10.0], [20.0, 30.0], [40.0, 42.0]]
    assert eras.intersect(11, 19) == []


def test_clip_prefers_segment_containing_anchor_then_first_intersection():
    eras = EraSet([[0, 10], [20, 30]])

    assert eras.clip(22, 5) == [20.0, 27.0]
    assert eras.clip(15, 8) == [7.0, 10.0]
    assert eras.clip(100, 2) == []


@pytest.mark.parametrize("step", [0, -1, -0.5])
def test_windows_reject_non_positive_step(step):
    with pytest.raises(ValueError):
        EraSet([[0, 10]]).windows(step)


def test_from_dates_rejects_reversed_or_invalid_dates():
    with pytest.raises(ValueError):
        EraSet.from_dates([["2001-01-02", "2001-01-01"]])
    with pytest.raises(ValueError):
        EraSet.from_dates([["not-a-date", "2001-01-01"]])
