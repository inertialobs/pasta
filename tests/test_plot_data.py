from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.requires_pykep


def _norm(v):
    return math.sqrt(sum(c * c for c in v))


def test_rtn_components_axes_are_orthonormal_and_reconstruct_vec():
    from orbcalc.decode_report import _rtn_components

    # r 沿 +x, v 沿 +y -> R=+x, N=+z, T=+y
    comp = _rtn_components([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [3.0, 4.0, 5.0])
    assert comp == [3.0, 4.0, 5.0]


def test_rtn_components_degenerate_returns_zero():
    from orbcalc.decode_report import _rtn_components

    assert _rtn_components([0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 2.0, 3.0]) == [0.0, 0.0, 0.0]
    # r 与 v 平行 -> 无轨道面
    assert _rtn_components([1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [1.0, 2.0, 3.0]) == [0.0, 0.0, 0.0]


def test_dsm_vectors_recover_impulse_vector():
    import pykep as pk

    from orbcalc.decode_report import dsm_vectors

    mu = pk.MU_SUN
    r0 = [pk.AU, 0.0, 0.0]
    v0 = [0.0, math.sqrt(mu / pk.AU), 0.0]
    dt_days = 100.0
    r_dsm, v_pre = pk.propagate_lagrangian([r0, v0], dt_days * pk.DAY2SEC, mu)
    delta = [100.0, -50.0, 20.0]
    v_post = [v_pre[k] + delta[k] for k in range(3)]

    vecs, rtns = dsm_vectors([(r0, v0), (r_dsm, v_post)], [0.0, dt_days], 1)
    assert vecs[0] == pytest.approx(delta, abs=1e-6)
    assert _norm(vecs[0]) == pytest.approx(_norm(delta), abs=1e-6)

    # R/T/N 正交基应当无失真地重建同一向量
    r_hat = [r_dsm[k] / math.sqrt(sum(c * c for c in r_dsm)) for k in range(3)]
    h = [
        r_dsm[1] * v_pre[2] - r_dsm[2] * v_pre[1],
        r_dsm[2] * v_pre[0] - r_dsm[0] * v_pre[2],
        r_dsm[0] * v_pre[1] - r_dsm[1] * v_pre[0],
    ]
    hn = math.sqrt(sum(c * c for c in h))
    n_hat = [c / hn for c in h]
    t_hat = [
        n_hat[1] * r_hat[2] - n_hat[2] * r_hat[1],
        n_hat[2] * r_hat[0] - n_hat[0] * r_hat[2],
        n_hat[0] * r_hat[1] - n_hat[1] * r_hat[0],
    ]
    dR, dT, dN = rtns[0]
    rebuilt = [dR * r_hat[k] + dT * t_hat[k] + dN * n_hat[k] for k in range(3)]
    assert rebuilt == pytest.approx(vecs[0], abs=1e-6)


def test_build_plot_json_contains_dsm_direction(monkeypatch):
    import pykep as pk

    from orbcalc import plot_data

    class _FakePlanet:
        def eph(self, when):
            return [pk.AU, 0.0, 0.0], [0.0, 0.0, 0.0]

    monkeypatch.setattr(plot_data, "get_planet", lambda tag: _FakePlanet())

    mu = pk.MU_SUN
    r0 = [pk.AU, 0.0, 0.0]
    v0 = [0.0, math.sqrt(mu / pk.AU), 0.0]
    r_dsm, v_pre = pk.propagate_lagrangian([r0, v0], 100.0 * pk.DAY2SEC, mu)
    delta = [100.0, -50.0, 20.0]
    v_post = [v_pre[k] + delta[k] for k in range(3)]
    cfg = SimpleNamespace(name="t", seq=["EARTH", "MARS"])
    info = {
        "epochs": [0.0, 200.0], "tofs": [200.0],
        "dsm": [_norm(delta)], "dsm_total": _norm(delta),
        "blegs": [(r0, v0), (r_dsm, v_post)], "bep": [0.0, 100.0],
        "dsm_vecs": [delta], "dsm_rtn": [[1.0, 2.0, 3.0]], "etas": [0.42],
    }
    data = plot_data.build_plot_json(cfg, info)
    assert data["dsm_arrow_au"] == plot_data.DSM_ARROW_AU
    dsm = data["legs"][0]["dsm"]
    assert dsm["vec"] == pytest.approx(delta, abs=1e-6)
    assert dsm["rtn"] == [1.0, 2.0, 3.0]
    assert dsm["unit"] == pytest.approx([c / _norm(delta) for c in delta], abs=1e-9)
    assert dsm["iso"] == str(pk.epoch(100.0).to_datetime())
    assert dsm["eta"] == 0.42

    info["dsm_vecs"] = [[0.0, 0.0, 0.0]]
    assert plot_data.build_plot_json(cfg, info)["legs"][0]["dsm"]["unit"] is None


def test_build_plot_json_without_direction_fields_still_works(monkeypatch):
    import pykep as pk

    from orbcalc import plot_data

    class _FakePlanet:
        def eph(self, when):
            return [pk.AU, 0.0, 0.0], [0.0, 0.0, 0.0]

    monkeypatch.setattr(plot_data, "get_planet", lambda tag: _FakePlanet())
    mu = pk.MU_SUN
    r0 = [pk.AU, 0.0, 0.0]
    v0 = [0.0, math.sqrt(mu / pk.AU), 0.0]
    r_dsm, v = pk.propagate_lagrangian([r0, v0], 100.0 * pk.DAY2SEC, mu)
    cfg = SimpleNamespace(name="t", seq=["EARTH", "MARS"])
    info = {
        "epochs": [0.0, 200.0], "tofs": [200.0],
        "dsm": [10.0], "dsm_total": 10.0,
        "blegs": [(r0, v0), (r_dsm, v)], "bep": [0.0, 100.0],
    }
    dsm = plot_data.build_plot_json(cfg, info)["legs"][0]["dsm"]
    assert "vec" not in dsm and "unit" not in dsm


def test_summarize_includes_rtn():
    from orbcalc.decode_report import summarize

    cfg = SimpleNamespace(name="t", seq=["EARTH", "MARS"], dsm_limit_ms=600.0)
    info = {
        "t0": 0.0, "tofs": [100.0], "epochs": [0.0, 100.0],
        "dsm": [10.0], "dsm_total": 10.0,
        "vinf_launch": 3000.0, "vinf_arr": 1000.0,
        "etas": [0.5], "dsm_rtn": [[1.0, 2.0, 3.0]],
        "bep": [0.0, 50.0],
    }
    out = summarize(info, cfg)
    assert out["legs"][0]["dsm_rtn_ms"] == [1.0, 2.0, 3.0]
    import pykep as pk
    assert out["legs"][0]["dsm_iso"] == str(pk.epoch(50.0).to_datetime())
    assert out["legs"][0]["dsm_elapsed_d"] == 50.0


def test_summarize_flyby_arrival_time_and_dsm_elapsed():
    from orbcalc.decode_report import summarize

    cfg = SimpleNamespace(name="t", seq=["EARTH", "VENUS", "MARS"],
                          dsm_limit_ms=600.0, safe_radius={})
    info = {
        "t0": 0.0, "tofs": [100.0, 200.0], "epochs": [0.0, 100.0, 300.0],
        "dsm": [10.0, 20.0], "dsm_total": 30.0,
        "vinf_launch": 3000.0, "vinf_arr": 1000.0,
        "etas": [0.5, 0.4], "rps": [1.5], "betas": [0.1],
        "bep": [0.0, 50.0, 180.0, 280.0],
    }
    out = summarize(info, cfg)
    import pykep as pk
    assert out["flybys"][0]["arrive_iso"] == str(pk.epoch(100.0).to_datetime())
    assert out["legs"][0]["dsm_elapsed_d"] == 50.0
    assert out["legs"][1]["dsm_elapsed_d"] == 280.0
