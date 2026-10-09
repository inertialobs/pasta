from __future__ import annotations

import numpy as np
import pytest

from orbcalc import mga
from orbcalc.config import TrajConfig
from orbcalc.mga import lambert_revs


def _cfg(seq, tof_bounds, types, **extra):
    return TrajConfig.from_dict({
        "seq": seq, "tof_bounds": tof_bounds, "lambert_types": types,
        "eras": [["2029-01-01", "2031-12-31"]],
        "objective": "min_dsm", "dsm_limit_ms": 5000, **extra,
    })


def _swept_deg(r0, v0, dt_days, n=2000):
    """Lambert 弧扫过的总角度 (度): 逐段累加位置矢量夹角, 多圈弧 > 360。"""
    import pykep as pk

    prev = np.asarray(r0, dtype=float)
    total = 0.0
    for k in range(1, n + 1):
        r, _ = pk.propagate_lagrangian([list(r0), list(v0)], dt_days * pk.DAY2SEC * k / n, pk.MU_SUN)
        r = np.asarray(r, dtype=float)
        total += np.arctan2(np.linalg.norm(np.cross(prev, r)), np.dot(prev, r))
        prev = r
    return float(np.degrees(total))


def _leg_sweeps(info):
    """每腿 DSM->到达 弧的扫过角 (度)."""
    out = []
    for i in range(len(info["tofs"])):
        r_dsm, v_post = info["blegs"][2 * i + 1]
        arc_days = info["epochs"][i + 1] - info["bep"][2 * i + 1]
        out.append(_swept_deg(r_dsm, v_post, arc_days))
    return out


def _native_pykep(cfg):
    """pykep 原生 mga_1dsm (不经 MGA1DSM 子类), 参数与 udp._make_trajopt 一致。"""
    import pykep as pk

    from orbcalc.planets import build_seq

    seq, _ = build_seq(cfg)
    era = cfg.era_set.ranges
    return pk.trajopt.mga_1dsm(
        seq=seq, tof_encoding="direct", t0=[float(era[0][0]), float(era[-1][1])],
        tof=[list(b) for b in cfg.tof_bounds], vinf=cfg.vinf_bounds_kmps,
        add_vinf_dep=False, add_vinf_arr=True, multi_objective=False, orbit_insertion=False,
        eta_bounds=cfg.eta_bounds, rp_ub=cfg.rp_ub)


def _same_result(a, b):
    """两次 _compute_dvs 结果逐位相同 (DV, T, 弹道腿, 历元)。"""
    np.testing.assert_array_equal(np.asarray(a[0]), np.asarray(b[0]))
    np.testing.assert_array_equal(np.asarray(a[2]), np.asarray(b[2]))
    np.testing.assert_array_equal(np.asarray(a[3], dtype=object), np.asarray(b[3], dtype=object))
    np.testing.assert_array_equal(np.asarray(a[4]), np.asarray(b[4]))


# ---------------------------------------------------------------------------
# 配置层 (不依赖 pykep)
# ---------------------------------------------------------------------------
def test_lambert_revs_maps_solution_index_to_revolutions():
    assert [lambert_revs(k) for k in (0, 1, 2, 3, 4)] == [0, 1, 1, 2, 2]


def test_default_lambert_types_are_single_rev_per_leg():
    cfg = TrajConfig()
    assert cfg.lambert_types == [0] * (len(cfg.seq) - 1)


def test_lambert_types_follow_leg_count_on_update():
    cfg = TrajConfig()
    cfg.update({
        "seq": ["EARTH", "VENUS", "EARTH"],
        "tof_bounds": [[100, 200], [100, 300]],
        "lambert_types": [1, 2],
    })
    assert cfg.lambert_types == [1, 2]

    # 新增节点而未给出类型: 新增腿补 0, 已有腿保持
    cfg.update({
        "seq": ["EARTH", "VENUS", "EARTH", "MARS"],
        "tof_bounds": [[100, 200], [100, 300], [200, 400]],
    })
    assert cfg.lambert_types == [1, 2, 0]

    # 删节点: 多出的腿类型被截断
    cfg.update({"seq": ["EARTH", "VENUS"], "tof_bounds": [[100, 200]]})
    assert cfg.lambert_types == [1]


def test_lambert_types_reject_unknown_values_and_keep_previous_config():
    cfg = TrajConfig()
    before = list(cfg.lambert_types)
    for bad in ([3, 0, 0, 0, 0], [0, 0, 0, 0, 0.5], [True, 0, 0, 0, 0], [-1, 0, 0, 0, 0]):
        with pytest.raises(ValueError):
            cfg.update({"lambert_types": bad})
    assert cfg.lambert_types == before


def test_pykep_version_note_only_for_unverified_versions():
    assert mga.pykep_version_note("3.0.1") is None
    note = mga.pykep_version_note("9.9.9")
    assert note and "9.9.9" in note


# ---------------------------------------------------------------------------
# pykep 相关: 精度 / 前向兼容
# ---------------------------------------------------------------------------
@pytest.mark.requires_pykep
def test_single_rev_matches_pykep_native_bit_for_bit():
    """纯 1/2 类: MGA1DSM._compute_dvs 与 pykep 3.0.1 原生 mga_1dsm 逐位相同。
    同时作为漂移检测: pykep 升级若改变单圈几何/解选择, 此测试失败, 提示同步 orbcalc/mga.py。"""
    from orbcalc.udp import _make_trajopt

    cfg = TrajConfig()
    native = _native_pykep(cfg)
    ours = _make_trajopt(cfg, None, None, cfg.vinf_bounds_kmps)
    assert ours.lambert_types == [0] * (len(cfg.seq) - 1)
    lb, ub = native.get_bounds()
    rng = np.random.default_rng(0)
    n_ok = 0
    for _ in range(40):
        x = list(rng.uniform(lb, ub))
        try:
            a = native._compute_dvs(x)
        except Exception as e:                       # noqa: BLE001
            with pytest.raises(type(e)):
                ours._compute_dvs(x)
            continue
        _same_result(a, ours._compute_dvs(x))
        n_ok += 1
    assert n_ok > 0


@pytest.mark.requires_pykep
def test_pretty_and_plot_refuse_multirev_but_pretty_delegates_for_single_rev(capsys):
    from orbcalc.udp import _make_trajopt

    cfg_single = TrajConfig()
    single = _make_trajopt(cfg_single, None, None, cfg_single.vinf_bounds_kmps)
    lb, ub = single.get_bounds()
    rng = np.random.default_rng(2)
    x = None
    for _ in range(300):
        cand = list(rng.uniform(lb, ub))
        try:
            single._compute_dvs(cand)
            x = cand
            break
        except Exception:                            # noqa: BLE001
            continue
    assert x is not None, "应能在随机样本中找到一个可计算的单圈向量"
    single.pretty(x)                                 # 委托 pykep, 不抛异常
    assert "DSM magnitude" in capsys.readouterr().out

    cfg_multi = _cfg(["EARTH", "VENUS"], [[300.0, 800.0]], [1])
    multi = _make_trajopt(cfg_multi, cfg_multi.era_set.ranges[0], None, cfg_multi.vinf_bounds_kmps)
    with pytest.raises(NotImplementedError, match="pretty"):
        multi.pretty(list(x))
    with pytest.raises(NotImplementedError, match="plot"):
        multi.plot(list(x))


@pytest.mark.requires_pykep
def test_missing_upstream_member_fails_fast(monkeypatch):
    from orbcalc.udp import _make_trajopt

    monkeypatch.setattr(mga, "PYKEP_REQUIRED_ATTRS", mga.PYKEP_REQUIRED_ATTRS + ("_no_such_member",))
    cfg = TrajConfig()
    with pytest.raises(RuntimeError, match="_no_such_member"):
        _make_trajopt(cfg, None, None, cfg.vinf_bounds_kmps)


@pytest.mark.requires_pykep
def test_solution_zero_is_independent_of_requested_revolutions():
    """拼接前提: 同一腿若因相邻腿需要多圈而以 multi_revs>0 求解, 其 0 圈解 (类型 1/2)
    必须与 multi_revs=0 时逐位一致, 否则混合序列中单圈腿会被静默改变。"""
    import pykep as pk

    from orbcalc.planets import get_planet

    earth, venus = get_planet("EARTH"), get_planet("VENUS")
    t0, tof = 10000.0, 500.0
    r0, _ = earth.eph(pk.epoch(t0))
    r1, _ = venus.eph(pk.epoch(t0 + tof))
    lp0 = pk.lambert_problem(r0, r1, tof * pk.DAY2SEC, pk.MU_SUN, cw=False, multi_revs=0)
    lp1 = pk.lambert_problem(r0, r1, tof * pk.DAY2SEC, pk.MU_SUN, cw=False, multi_revs=1)
    assert len(lp1.v0) >= 3, "该几何下应存在 1 圈解"
    np.testing.assert_allclose(lp1.v0[0], lp0.v0[0], rtol=0, atol=1e-9)
    np.testing.assert_allclose(lp1.v1[0], lp0.v1[0], rtol=0, atol=1e-9)


# ---------------------------------------------------------------------------
# pykep 相关: 多圈实例与拼接
# ---------------------------------------------------------------------------
@pytest.mark.requires_pykep
def test_single_leg_optimum_can_be_a_one_rev_transfer():
    """实例: EARTH->VENUS 单腿, 只允许类型 3 (1 圈低能). SADE 固定种子搜索,
    最优解的 DSM->到达 弧扫过角应在 (360, 720) 度, 即恰好 1 圈 + 部分弧。"""
    from orbcalc.decode_report import decode, summarize
    from orbcalc.engines import run_sade
    from orbcalc.udp import TOF_UDP

    cfg = _cfg(["EARTH", "VENUS"], [[300.0, 800.0]], [1])
    udp = TOF_UDP(cfg, t0=cfg.era_set.ranges[0])
    f, x = run_sade(udp, gen=600, pop_size=40, runs=1, seed_base=1)
    assert f < 1e12, "类型 3 在该 TOF 盒内应可行"

    info = decode(x, udp.udp)
    assert len(info["lamberts"][0].v0) >= 3          # 1 圈解确实存在于该 TOF
    sweep = _leg_sweeps(info)[0]
    assert 360.0 < sweep < 720.0
    assert info["dsm_total"] == pytest.approx(f, rel=1e-9)

    leg = summarize(info, cfg)["legs"][0]
    assert leg["lambert_type"] == 1

    # 同一决策向量改用类型 1/2 (单圈): 扫过角 < 360, 轨迹与 DSM 随之改变
    cfg0 = _cfg(["EARTH", "VENUS"], [[300.0, 800.0]], [0])
    info0 = decode(x, TOF_UDP(cfg0, t0=cfg0.era_set.ranges[0]).udp)
    assert _leg_sweeps(info0)[0] < 360.0
    assert info0["dsm_total"] != pytest.approx(info["dsm_total"], rel=1e-6)


@pytest.mark.requires_pykep
def test_lambert_type_applies_to_its_own_leg_only():
    """两腿 EARTH->VENUS->EARTH: 类型 [1, 0] 时仅第 0 腿为 1 圈, 第 1 腿仍为单圈; 反之亦然。"""
    from orbcalc.decode_report import decode
    from orbcalc.engines import run_sade
    from orbcalc.udp import TOF_UDP

    cfg = _cfg(["EARTH", "VENUS", "EARTH"], [[300.0, 800.0], [300.0, 800.0]], [1, 0])
    udp = TOF_UDP(cfg, t0=cfg.era_set.ranges[0])
    f, x = run_sade(udp, gen=300, pop_size=30, runs=1, seed_base=5)
    assert f < 1e12
    info = decode(x, udp.udp)
    sweeps = _leg_sweeps(info)
    assert 360.0 < sweeps[0] < 720.0
    assert sweeps[1] < 360.0
    assert [len(l.v0) >= 3 for l in info["lamberts"]] == [True, False]


@pytest.mark.requires_pykep
@pytest.mark.parametrize("types", [[1, 0], [0, 1], [1, 2], [2, 1]])
def test_mixed_single_and_multirev_legs_join_continuously_at_flyby(types):
    """拼接: 1/2 类与 3/4 类相邻时, 各腿在交会点处连续。
    - 到达弧终点 = 交会行星星历位置 (精确到 1 m 量级);
    - 借力保持行星相对速率 |v_out - v_pl| = |v_in - v_pl|, 其中 v_in 取所选分支;
    - 绘图数据 (plot.json 的 lambert 折线) 终点与行星位置一致。"""
    import pykep as pk

    from orbcalc.decode_report import decode
    from orbcalc.engines import run_sade
    from orbcalc.plot_data import build_plot_json
    from orbcalc.planets import get_planet
    from orbcalc.udp import TOF_UDP

    cfg = _cfg(["EARTH", "VENUS", "EARTH"], [[300.0, 800.0], [300.0, 800.0]], types)
    udp = TOF_UDP(cfg, t0=cfg.era_set.ranges[0])
    f, x = run_sade(udp, gen=300, pop_size=30, runs=1, seed_base=5)
    assert f < 1e12, f"{types} 应可行"
    info = decode(x, udp.udp)

    for i in range(len(types) - 1):
        pla = get_planet(cfg.seq[i + 1])
        r_pl, v_pl = pla.eph(pk.epoch(info["epochs"][i + 1]))
        r_dsm, v_post = info["blegs"][2 * i + 1]
        dt_l = (info["epochs"][i + 1] - info["bep"][2 * i + 1]) * pk.DAY2SEC
        r_end, _ = pk.propagate_lagrangian([r_dsm, v_post], dt_l, pk.MU_SUN)
        assert np.linalg.norm(np.asarray(r_end) - np.asarray(r_pl)) < 1.0      # m

        _, v_out = info["blegs"][2 * (i + 1)]
        v_in = info["lamberts"][i].v1[types[i]]
        s_in = np.linalg.norm(np.asarray(v_in) - np.asarray(v_pl))
        s_out = np.linalg.norm(np.asarray(v_out) - np.asarray(v_pl))
        assert s_out == pytest.approx(s_in, abs=1e-6)                           # m/s

    plot = build_plot_json(cfg, info)
    for i in range(len(types)):
        lam = plot["legs"][i]["lambert"]
        if not lam["x"]:
            continue
        pla = get_planet(cfg.seq[i + 1])
        r_pl, _ = pla.eph(pk.epoch(info["epochs"][i + 1]))
        end = np.array([lam["x"][-1], lam["y"][-1], lam["z"][-1]])
        np.testing.assert_allclose(end, np.asarray(r_pl) / pk.AU, atol=1e-9)


@pytest.mark.requires_pykep
def test_infeasible_multirev_is_penalized_in_fitness_and_raises_in_decode():
    """TOF 盒过短, 不存在 1 圈解: fitness 判不可行 (1e12), 直接 _compute_dvs 抛 ValueError。"""
    from orbcalc.udp import TOF_UDP

    cfg = _cfg(["EARTH", "VENUS"], [[100.0, 200.0]], [1])
    udp = TOF_UDP(cfg, t0=cfg.era_set.ranges[0])
    lb, ub = udp.get_bounds()
    rng = np.random.default_rng(3)
    for _ in range(10):
        x = list(rng.uniform(lb, ub))
        assert udp.fitness(x) == [1e12]
        with pytest.raises(ValueError, match="Lambert"):
            udp.udp._compute_dvs(x)
