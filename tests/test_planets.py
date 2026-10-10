from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.requires_pykep

# 8 大行星: TAG -> 原 jpl_lp 名 (回归基准)
JL_NAMES = {
    "MERCURY": "mercury", "VENUS": "venus", "EARTH": "earth", "MARS": "mars",
    "JUPITER": "jupiter", "SATURN": "saturn", "URANUS": "uranus", "NEPTUNE": "neptune",
}


def _jpl_lp(name):
    import pykep as pk
    return pk.planet(pk.udpla.jpl_lp(name))


def test_physical_params_match_jpl_lp_bit_for_bit():
    """新表 (ConstPhys) 的 μ_self/半径/中心 μ 与 pk.udpla.jpl_lp 逐位一致。"""
    from orbcalc.planets import get_planet

    for tag, name in JL_NAMES.items():
        ref = _jpl_lp(name)
        p = get_planet(tag)
        assert p.mu_self == ref.mu_self, tag
        assert p.radius == ref.radius, tag
        assert p.mu_central_body == ref.mu_central_body, tag


def test_build_seq_safe_radius_matches_previous_registry_values():
    """轨迹用行星的 safe_radius 与改造前 REGISTRY 值逐位一致 (含 URANUS 回退值)。"""
    from orbcalc.planets import BODY_PARAMS, build_seq

    cfg = SimpleNamespace(seq=list(JL_NAMES.keys()), safe_radius={})
    seq, names = build_seq(cfg)
    assert names == [t.capitalize() for t in JL_NAMES]
    for pl, tag in zip(seq, JL_NAMES):
        assert pl.safe_radius == BODY_PARAMS[tag]["safe_radius"], tag

    # 关键字面值 (改造前 REGISTRY)
    expect = {
        "MERCURY": 2640000.0, "VENUS": 6252000.0, "EARTH": 6578137.0, "MARS": 3589500.0,
        "JUPITER": 142984000.0, "SATURN": 116464000.0, "NEPTUNE": 49244000.0,
        "URANUS": 27898200.000000004,
    }
    assert {t: BODY_PARAMS[t]["safe_radius"] for t in JL_NAMES} == expect


def test_safe_radius_cfg_override_wins():
    from orbcalc.planets import planet_safe_radius

    cfg = SimpleNamespace(safe_radius={"EARTH": 1.0e7})
    assert planet_safe_radius("EARTH", cfg) == 1.0e7
    # 未覆盖 → 表值
    assert planet_safe_radius("MARS", cfg) == 3589500.0


def test_known_tags_and_unknown_rejected():
    from orbcalc.planets import known_tags
    from orbcalc.config import TrajConfig

    assert set(JL_NAMES) <= known_tags()
    with pytest.raises(ValueError):
        TrajConfig.from_dict({"seq": ["EARTH", "NOPE"], "tof_bounds": [[100, 200]],
                              "eras": [["2029-01-01", "2030-01-01"]]})
