from __future__ import annotations

import pytest

from orbcalc.settings import Settings, resolve_compute


def test_compute_override_is_applied_without_changing_other_defaults():
    resolved = resolve_compute({}, jobs=2)

    assert resolved["jobs"] == 2
    assert resolved["scan_keep_pct"] == 80
    assert resolved["refine_keep_pct"] == 80


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scan_keep_pct", 0),
        ("refine_keep_pct", 101),
        ("jobs", 0),
        ("era_step_d", 0),
        ("t0_coverage", [1.0, 0.5]),
    ],
)
def test_compute_validation_rejects_invalid_values(field, value):
    settings = Settings()
    with pytest.raises(ValueError):
        settings.update({field: value})


def test_failed_update_is_transactional():
    settings = Settings()
    original = dict(settings)

    with pytest.raises(ValueError):
        settings.update({"jobs": 0, "era_step_d": 10})

    assert dict(settings) == original
