from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.requires_pykep


@pytest.fixture
def kernel_dir(monkeypatch):
    """把 ephem 的星历目录指向 pykep 自带 de440s.bsp 所在目录 (免拷贝)。"""
    import pykep as pk

    from orbcalc import ephem, planets

    d = Path(pk.udpla.de440s.kernel_file()).parent
    monkeypatch.setattr(ephem, "dir_path", lambda: d)
    ephem.reset_cache()
    yield d
    ephem.reset_cache()
    planets.set_active([])
    ephem._loaded.clear()


def test_tag_from_name():
    from orbcalc.ephem import tag_from_name

    assert tag_from_name("EARTH") == "EARTH"
    assert tag_from_name("1 Ceres") == "1_CERES"
    assert tag_from_name("NAIF 2000001") == "NAIF_2000001"
    assert tag_from_name("  ") is None


def test_scan_one_de440s_lists_bodies_and_coverage(kernel_dir):
    import pykep as pk

    from orbcalc import ephem

    info = ephem._scan_one(Path(pk.udpla.de440s.kernel_file()))
    assert info["file"] == "de440s.bsp" and info["size"] > 0
    naifs = {b["naif"] for b in info["bodies"]}
    assert {399, 10, 301} <= naifs                     # EARTH / SUN / MOON
    names = {b["name"] for b in info["bodies"]}
    assert {"EARTH", "SUN", "MOON"} <= names
    assert info["coverage"] is not None


def test_enabling_kernel_adds_bodies_and_builds_planet(kernel_dir):
    import pykep as pk

    from orbcalc import planets

    planets.set_active(["de440s.bsp"])
    known = planets.known_tags()
    assert "EARTH" in known            # 内置 (按 NAIF 去重)
    assert "MOON" in known             # kernel 新增

    info = planets.body_info("MOON")
    assert info is not None and info["kernel"] == "de440s.bsp" and info["builtin"] is False

    pl = planets.get_planet("MOON")
    r, v = pl.eph(pk.epoch(0.0))
    assert len(r) == 3 and (r[0] ** 2 + r[1] ** 2 + r[2] ** 2) > 0

    seq, names = planets.build_seq(SimpleNamespace(seq=["EARTH", "MOON"], safe_radius={}))
    assert names == ["Earth", "Moon"]
    assert seq[1].mu_self == 0.0 and seq[1].radius == 0.0 and seq[1].safe_radius == 0.0


def test_config_accepts_only_enabled_kernel_tags(kernel_dir):
    from orbcalc import planets
    from orbcalc.config import TrajConfig

    data = {"seq": ["EARTH", "MOON"], "tof_bounds": [[100, 200]],
            "eras": [["2029-01-01", "2030-01-01"]]}

    planets.set_active(["de440s.bsp"])
    assert TrajConfig.from_dict(data).seq == ["EARTH", "MOON"]

    planets.set_active([])
    with pytest.raises(ValueError):
        TrajConfig.from_dict(data)


def test_bodies_endpoint_lists_builtin_and_kernel(kernel_dir):
    from webapp.app import app

    from orbcalc import planets

    planets.set_active(["de440s.bsp"])
    client = app.test_client()
    got = client.get("/api/bodies").get_json()
    assert "EARTH" in got and got["EARTH"]["builtin"] is True
    assert "MOON" in got and got["MOON"]["builtin"] is False


def test_massless_asteroid_waypoint(kernel_dir):
    """μ=0/半径=0 的天体 (此处用 kernel 里的 MOON) 可作中间定时路点:
    rp 下界护为 0, 边界有限, 优化器可算且可解出轨迹。"""
    import numpy as np

    from orbcalc import planets
    from orbcalc.config import TrajConfig
    from orbcalc.decode_report import decode
    from orbcalc.engines import run_sade
    from orbcalc.udp import TOF_UDP

    planets.set_active(["de440s.bsp"])
    cfg = TrajConfig.from_dict({
        "seq": ["EARTH", "MOON", "EARTH"],
        "tof_bounds": [[2.0, 40.0], [2.0, 40.0]],
        "eras": [["2029-01-01", "2029-12-31"]],
        "objective": "min_dsm", "dsm_limit_ms": 5000,
    })
    udp = TOF_UDP(cfg, t0=cfg.era_set.ranges[0])
    lb, ub = udp.udp.get_bounds()
    assert lb[7] == 0.0                                    # MOON radius=0 → rp 下界 0
    assert np.isfinite(np.asarray(lb, dtype=float)).all()
    assert np.isfinite(np.asarray(ub, dtype=float)).all()

    f, x = run_sade(udp, gen=150, pop_size=24, runs=1, seed_base=1)
    assert f < 1e12, "零质量路点应可行"
    info = decode(x, udp.udp)
    assert len(info["tofs"]) == 2


def test_local_refine_clamps_out_of_bounds_xnes():
    """xnes 可能返回越界 champion (min_tof 把 TOF 压到盒下界时): local_refine 必须
    clamp 到边界并返回与之一致的目标值, 否则下游会用越界 TOF (曾导致倒置盒崩溃)。"""
    import numpy as np

    from orbcalc.config import TrajConfig
    from orbcalc.engines import local_refine
    from orbcalc.udp import TOF_UDP

    cfg = TrajConfig.from_dict({
        "seq": ["EARTH", "MARS"], "tof_bounds": [[180.0, 900.0]],
        "eras": [["2029-01-01", "2030-01-01"]], "objective": "min_tof"})
    udp = TOF_UDP(cfg, t0=cfg.era_set.ranges[0])
    lb, ub = (np.asarray(v, float) for v in udp.get_bounds())
    f, x = local_refine(udp, list(lb), "xnes")
    x = np.asarray(x, float)
    assert (x >= lb).all() and (x <= ub).all()
    assert f == udp.fitness(list(x))[0]


def test_moonless_barycenter_aliases_to_builtin(kernel_dir):
    """无卫星行星的质心 NAIF (1/2) 视为内置本体: 不产生 *_BARYCENTER 冗余项,
    VENUS 仍走内置 (继承 μ/半径)。kernel 无旁挂天体则标注"仅定时路点"。"""
    from orbcalc import planets

    planets.set_active(["de440s.bsp"])
    kb = planets._kernel_bodies()
    assert "VENUS_BARYCENTER" not in kb and "MERCURY_BARYCENTER" not in kb
    assert kb["VENUS"]["naif"] == 2                 # 质心被映射到内置 TAG
    assert planets.body_info("VENUS")["builtin"] is True
    assert planets.body_info("VENUS_BARYCENTER") is None
    assert planets.body_info("MOON")["builtin"] is False
    assert "无 μ/半径" in planets.body_info("MOON")["note"]


def test_sidecar_bodies_old_and_multi():
    from orbcalc.ephem import _sidecar_bodies

    old = {"naif": 2162173, "name": "Ryugu", "mu_self": 3e9}
    assert _sidecar_bodies(old) == {2162173: old}

    multi = {"2000001": {"naif": 2000001, "name": "Ceres", "mu_self": 6.2e10},
             "2000004": {"naif": 2000004, "name": "Vesta"}}
    got = _sidecar_bodies(multi)
    assert got[2000001]["name"] == "Ceres" and got[2000004]["name"] == "Vesta"

    wrapped = {"bodies": multi}
    assert _sidecar_bodies(wrapped)[2000001]["name"] == "Ceres"


def _no_cover(*_a):
    raise RuntimeError("no coverage")


def test_scan_one_uses_multi_body_sidecar(tmp_path, monkeypatch):
    import pykep as pk

    from orbcalc import ephem

    p = tmp_path / "sb441-n16.bsp"
    p.write_bytes(b"")
    (tmp_path / "sb441-n16.json").write_text(
        '{"2000001": {"naif": 2000001, "name": "Ceres", "mu_self": 6.2e10}}',
        encoding="utf-8")
    monkeypatch.setattr(pk.utils, "inspect_spice_kernel", lambda _p: [2000001])
    monkeypatch.setattr(pk.utils, "extract_coverage_window", _no_cover)

    info = ephem._scan_one(p)
    assert info["bodies"][0]["name"] == "Ceres"
    assert info["bodies"][0]["mu_self"] == 6.2e10
    ephem._loaded.clear()


def test_scan_one_old_sidecar_unchanged(tmp_path, monkeypatch):
    import pykep as pk

    from orbcalc import ephem

    p = tmp_path / "ryugu.bsp"
    p.write_bytes(b"")
    (tmp_path / "ryugu.json").write_text(
        '{"naif": 2162173, "name": "Ryugu", "mu_self": 3e9, "radius": 448.0, '
        '"safe_radius": 470.4}', encoding="utf-8")
    monkeypatch.setattr(pk.utils, "inspect_spice_kernel", lambda _p: [2162173])
    monkeypatch.setattr(pk.utils, "extract_coverage_window", _no_cover)

    body = ephem._scan_one(p)["bodies"][0]
    assert body == {"naif": 2162173, "name": "Ryugu", "mu_self": 3e9,
                    "radius": 448.0, "safe_radius": 470.4}
    ephem._loaded.clear()


def test_display_keeps_numbered_names():
    from orbcalc.planets import _display

    assert _display("52 Europa") == "52 Europa"
    assert _display("2002 MS4") == "2002 MS4"
    assert _display("mercury") == "Mercury"
    assert _display("EARTH") == "Earth"
