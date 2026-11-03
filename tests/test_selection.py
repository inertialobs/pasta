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


def test_select_key_orderings():
    from orbcalc.stages import select_key

    cases = [
        ("min_tof", [1, 0], ["short", "low-dsm", "long", "infeasible"]),
        ("min_dsm", [0, 1], ["low-dsm", "long", "short", "infeasible"]),
        ("custom", [1, 1], ["low-dsm", "long", "short", "infeasible"]),
    ]
    candidates = {
        "short": {"tofs": [100], "dsm_total": 500},
        "low-dsm": {"tofs": [150], "dsm_total": 100},
        "long": {"tofs": [300], "dsm_total": 200},
        "infeasible": {"tofs": [50], "dsm_total": 700},
    }
    for objective, weights, expected in cases:
        cfg = SimpleNamespace(objective=objective, objective_weights=weights,
                              dsm_limit_ms=600)
        ordered = sorted(candidates, key=lambda name: select_key(cfg, candidates[name]))
        assert ordered == expected, objective
