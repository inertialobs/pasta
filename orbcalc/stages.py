# -*- coding: utf-8 -*-
"""
六阶段流水线 (多进程版) — 逻辑与 temp/EVVEJU_TOF_1DSM_mp.py 逐位一致,
所有硬编码 (TOF_BOUNDS / ERAS / 权重 / 种子节奏) 改为 cfg 驱动。

    [1] 扫描      : 每个发射窗口一轮 sade -> 并行任务
    [2] 细化      : 各窗口 sade runs 并行; 局部级联串行; multistart 种子并行
    [3] 弹道播种  : DSM-最小化 -> 低 DSM 种子
    [4/5] 压缩    : 宽/紧 TOF 盒 + 默认/强罚 (sade runs 与 multistart 并行)
    [6] 汇总      : pick_best (TOF 最小且 DSM<=limit; 否则目标最小)
"""
from __future__ import annotations

import numpy as np
import pykep as pk

from . import slog

from .engines import (_run_sade_task, _build_udp, run_sade, local_refine,
                      multistart_mp, narrow_tof_box)
from .udp import TOF_UDP, DSM_UDP
from .decode_report import decode


def phase_scan_mp(executor, cfg):
    """发射窗口粗扫: 每个窗口一轮 sade -> 并行任务."""
    step = 90.0 if cfg.smoke else 60.0
    if cfg.era_step_d is not None:
        step = float(cfg.era_step_d)
    gen = 60 if cfg.smoke else 200
    pop = 12 if cfg.smoke else 24
    runs = 1 if cfg.smoke else 2
    slog.inf(f"[scan] step={step:.0f}d sade(gen={gen},pop={pop}) x{runs} [parallel]")
    ranges = cfg.era_mjd()
    windows = []
    for (t0_lo, t0_hi) in ranges:
        slog.inf(f"  window {pk.epoch(t0_lo).to_datetime().date()} .. "
                 f"{pk.epoch(t0_hi).to_datetime().date()}")
        t = t0_lo
        while t <= t0_hi:
            windows.append(t)
            t += step
    # 每个 (窗口, run) 一个任务; 种子与串行 run_sade(seed_base=0) 完全一致
    futs = []
    for t in windows:
        for r in range(runs):
            futs.append((t, r, executor.submit(
                _run_sade_task, "tof", cfg, [t - 30.0, t + 30.0], None, None, None,
                gen, pop, r)))
    best_by_t = {}
    for t, r, fut in futs:
        f, x = fut.result()
        if f < 1e12 and (t not in best_by_t or f < best_by_t[t][0]):
            best_by_t[t] = (f, x)
    results = []
    for t in windows:
        if t not in best_by_t:
            continue
        f, x = best_by_t[t]
        udp = TOF_UDP(cfg, t0=[t - 30.0, t + 30.0])
        info = decode(x, udp.udp)
        results.append((f, t, x, info))
        slog.inf(f"  t0={pk.epoch(t).to_datetime().date()}  obj={f:9.0f} d  "
                 f"TOF={sum(info['tofs']):7.0f} d ({sum(info['tofs']) / 365.25:5.1f} yr)  "
                 f"DSM={info['dsm_total']:7.0f} m/s")
    results.sort(key=lambda r: r[0])
    slog.inf(f"\n[scan] top {cfg.scan_keep} by objective:")
    for f, t, x, info in results[:cfg.scan_keep]:
        slog.inf(f"  t0={pk.epoch(t).to_datetime().date()}  TOF={sum(info['tofs']):.0f} d "
                 f"({sum(info['tofs']) / 365.25:.2f} yr)  DSM={info['dsm_total']:.0f} m/s")
    return results[:cfg.scan_keep]


def phase_refine_mp(executor, cfg, cands):
    """细化: 各窗口 sade 的 runs 并行; 局部级联串行; multistart 种子并行."""
    gen = 60 if cfg.smoke else 600
    pop = 12 if cfg.smoke else 40
    runs = 1 if cfg.smoke else 3
    best_overall = None
    # ---- 阶段 A: 全部窗口的 sade runs 并行 ----
    sade_best = {}
    futs = []
    for rank, (f0, t0c, x0, info0) in enumerate(cands[:cfg.refine_keep]):
        tof_n = narrow_tof_box(x0, cfg, pct=0.15)
        sade_best[rank] = None
        for rr in range(runs):
            futs.append((rank, executor.submit(
                _run_sade_task, "tof", cfg, [t0c - 15.0, t0c + 15.0], tof_n,
                None, None, gen, pop, rr)))
    for rank, fut in futs:
        f, x = fut.result()
        if f < 1e12 and (sade_best[rank] is None or f < sade_best[rank][0]):
            sade_best[rank] = (f, x)
    # ---- 阶段 B: 每窗口 局部级联(串行) + multistart(种子并行) ----
    for rank, (f0, t0c, x0, info0) in enumerate(cands[:cfg.refine_keep]):
        tof_n = narrow_tof_box(x0, cfg, pct=0.15)
        sb = sade_best.get(rank)
        if sb is None:
            slog.wrn(f"  window #{rank + 1}: sade all failed, skip")
            continue
        f, x = sb
        udp = _build_udp("tof", cfg, [t0c - 15.0, t0c + 15.0], tof_n, None, None)
        slog.inf(f"\n[refine] window #{rank + 1} t0~{pk.epoch(t0c).to_datetime().date()} "
                 f"sade(gen={gen},pop={pop}) x{runs}")
        for loc in ("sbplx", "compass", "xnes"):
            try:
                fl, xl = local_refine(udp, x, loc)
                if fl < f:
                    f, x = fl, xl
            except Exception:
                pass
        res = multistart_mp(executor, "tof", cfg, [t0c - 15.0, t0c + 15.0], tof_n,
                            None, None, x, n_seeds=50, maxeval=2000,
                            seed=rank * 100 + 7)
        if res is not None and res[0] < f:
            f, x = res
        info = decode(x, udp.udp)
        slog.inf(f"  -> TOF={sum(info['tofs']):.0f} d ({sum(info['tofs']) / 365.25:.2f} yr)  "
                 f"DSM={info['dsm_total']:.0f} m/s")
        if best_overall is None or f < best_overall[0]:
            best_overall = (f, x, info, udp)
    return best_overall


def phase_ballistic_seed_mp(executor, cfg, x_ref):
    """弹道播种: 在参考窗口内最小化 DSM, 得到低 DSM 种子解 (并行)."""
    t0c = float(x_ref[0])
    tof_n = narrow_tof_box(x_ref, cfg, pct=0.15)
    gen = 60 if cfg.smoke else 500
    pop = 12 if cfg.smoke else 40
    runs = 1 if cfg.smoke else 3
    slog.inf(f"\n[ballistic seed] DSM-min at t0~{pk.epoch(t0c).to_datetime().date()} "
             f"sade(gen={gen},pop={pop}) x{runs} [parallel]")
    futs = [executor.submit(_run_sade_task, "dsm", cfg, [t0c - 10.0, t0c + 10.0], tof_n,
                            None, None, gen, pop, rr) for rr in range(runs)]
    f, x = None, None
    for fut in futs:
        ff, xx = fut.result()
        if ff < 1e12 and (f is None or ff < f):
            f, x = ff, xx
    if f is None:
        slog.wrn("  [seed] sade all failed, use x_ref")
        f, x = 0.0, list(x_ref)
    res = multistart_mp(executor, "dsm", cfg, [t0c - 10.0, t0c + 10.0], tof_n,
                        None, None, x, n_seeds=50, maxeval=2000, seed=3)
    if res is not None and res[0] < f:
        f, x = res
    udp = DSM_UDP(cfg, t0=[t0c - 10.0, t0c + 10.0], tof=tof_n)
    info = decode(x, udp.udp)
    slog.inf(f"  -> DSM={info['dsm_total']:.0f} m/s  TOF={sum(info['tofs']):.0f} d "
             f"({sum(info['tofs']) / 365.25:.2f} yr)")
    return x


def compress_pass_mp(executor, cfg, seed_x, tag, w1, w2, smoke=None,
                     gen=800, pop=48, runs=3, nseeds=60, pct=0.25):
    """[4/5] 从种子解出发压 TOF: 各腿 TOF 收窄到种子值 ±pct (不越全局边界)
    + 强/默认罚 (sade runs 与 multistart 并行). 宽压缩用大 pct, 紧压缩用小 pct."""
    if smoke is None:
        smoke = cfg.smoke
    t0c = float(seed_x[0])
    tofs_cur = [float(seed_x[5 + 4 * i]) for i in range(len(cfg.seq) - 1)]
    tof_n = []
    for (lo, hi), t in zip(cfg.tof_bounds, tofs_cur):
        tof_n.append([max(float(lo), t * (1 - pct)), min(float(hi), t * (1 + pct))])
    g = 60 if smoke else gen
    p = 12 if smoke else pop
    r = 1 if smoke else runs
    ns = 10 if smoke else nseeds
    slog.inf(f"\n[{tag}] from TOF={sum(tofs_cur):.0f} d, TOF box ±{pct:.0%}, "
             f"penalty({w1},{w2}), sade(gen={g},pop={p}) x{r} [parallel]")
    futs = [executor.submit(_run_sade_task, "tof", cfg, [t0c - 8.0, t0c + 8.0], tof_n,
                            w1, w2, g, p, 700 + rr) for rr in range(r)]
    f, x = None, None
    for fut in futs:
        ff, xx = fut.result()
        if ff < 1e12 and (f is None or ff < f):
            f, x = ff, xx
    if f is None:
        slog.wrn("  [compress] sade all failed, use seed")
        f, x = 0.0, list(seed_x)
    # 3 轮链式 multistart (轮次串行保持与串行版相同语义; 轮内种子并行)
    for s in range(3):
        res = multistart_mp(executor, "tof", cfg, [t0c - 8.0, t0c + 8.0], tof_n,
                            w1, w2, x, n_seeds=ns, maxeval=2500, seed=900 + s * 50)
        if res is not None and res[0] < f:
            f, x = res
    udp2 = TOF_UDP(cfg, t0=[x[0] - 4.0, x[0] + 4.0], tof=tof_n, w1=w1, w2=w2)
    for loc in ("sbplx", "cobyla", "xnes"):
        try:
            fl, xl = local_refine(udp2, x, loc, iters=3000)
            if fl < f:
                f, x = fl, xl
        except Exception:
            pass
    info = decode(x, udp2.udp)
    slog.inf(f"  -> TOF={sum(info['tofs']):.0f} d ({sum(info['tofs']) / 365.25:.2f} yr)  "
             f"DSM={info['dsm_total']:.0f} m/s")
    return x


def select_key(cfg, info):
    """最终选解键 (与 cfg.objective 对齐): (DSM 超限惩罚, 主目标, 总 TOF).

    - min_tof : 可行解中 TOF 最小 (与原行为一致)
    - min_dsm : 可行解中总 DSM 最小
    - custom  : 可行解中 w_tof*TOF + w_dsm*DSM 最小
    """
    tof = float(sum(info["tofs"]))
    dsm = float(info["dsm_total"])
    feasible = 0 if dsm <= cfg.dsm_limit_ms else 1
    if cfg.objective == "min_dsm":
        return (feasible, dsm, tof)
    if cfg.objective == "custom":
        w = cfg.objective_weights
        return (feasible, float(w[0]) * tof + float(w[1]) * dsm, tof)
    return (feasible, tof, 0.0)


def pick_best(cfg, candidates):
    """候选池中按 cfg.objective 选优 (DSM 超限的候选整体劣后)."""
    best = None
    best_key = None
    for x, udp in candidates:
        info = decode(x, udp.udp)
        key = select_key(cfg, info)
        if best_key is None or key < best_key:
            best, best_key = (info, x, udp), key
    return best
