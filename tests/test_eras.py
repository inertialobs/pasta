from __future__ import annotations

import pytest

from orbcalc.eras import EraSet


def test_eras_geometry():
    eras = EraSet([[0, 10], [20, 30], [40, 50]])

    assert EraSet([[0, 10], [20, 25]]).windows(4) == [0.0, 4.0, 8.0, 20.0, 24.0]
    assert eras.intersect(8, 42) == [[8.0, 10.0], [20.0, 30.0], [40.0, 42.0]]
    assert eras.intersect(11, 19) == []
    assert EraSet([[0, 10], [20, 30]]).clip(22, 5) == [20.0, 27.0]
    assert EraSet([[0, 10], [20, 30]]).clip(15, 8) == [7.0, 10.0]
    assert eras.clip(100, 2) == []


def test_windows_reject_non_positive_step():
    for step in (0, -1, -0.5):
        with pytest.raises(ValueError):
            EraSet([[0, 10]]).windows(step)


def test_from_dates_rejects_reversed_or_invalid_dates():
    with pytest.raises(ValueError):
        EraSet.from_dates([["2001-01-02", "2001-01-01"]])
    with pytest.raises(ValueError):
        EraSet.from_dates([["not-a-date", "2001-01-01"]])
