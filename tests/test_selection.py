from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.requires_pykep


def test_keep_n_rounds_and_clamps_percentage():
    from orbcalc.stages import _keep_n

    assert _keep_n(0, 10) == 1
    assert _keep_n(10, 10) == 1
    assert _keep_n(25, 10) == 3
    assert _keep_n(100, 10) == 10
    assert _keep_n(50, 0) == 0


@pytest.mark.parametrize(
    ("objective", "weights", "expected_order"),
    [
        ("min_tof", [1, 0], ["short", "low-dsm", "long", "infeasible"]),
        ("min_dsm", [0, 1], ["low-dsm", "long", "short", "infeasible"]),
        ("custom", [1, 1], ["low-dsm", "long", "short", "infeasible"]),
    ],
)
def test_select_key_uses_objective_and_places_infeasible_candidates_last(objective, weights, expected_order):
    from orbcalc.stages import select_key

    cfg = SimpleNamespace(objective=objective, objective_weights=weights, dsm_limit_ms=600)
    candidates = {
        "short": {"tofs": [100], "dsm_total": 500},
        "low-dsm": {"tofs": [150], "dsm_total": 100},
        "long": {"tofs": [300], "dsm_total": 200},
        "infeasible": {"tofs": [50], "dsm_total": 700},
    }
    ordered = sorted(candidates, key=lambda name: select_key(cfg, candidates[name]))

    assert ordered == expected_order
