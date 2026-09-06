# -*- coding: utf-8 -*-
"""
优化引擎 (与 mp 脚本逐位一致): sade / nlopt(sbplx, cobyla) / compass / xnes,
以及供进程池调用的模块级 worker 任务 (_run_sade_task / _sbplx_task)。

并行约定:
    * 任务参数只有小配置 (kind, cfg, t0, tof, w1, w2, 种子/起点) — cfg 为纯
      dataclass 可 pickle; 行星/UDP 在各 worker 内自行重建 (spawn 安全)。
    * _run_sade_task 与串行 run_sade(runs=1, seed_base=seed) 等价。
    * _sbplx_task 与串行 multistart 单起点行为等价; multistart_mp 的起点
      生成序列与串行版逐位一致。
"""
from __future__ import annotations

import numpy as np
import pygmo as pg

from . import slog
from .udp import TOF_UDP, DSM_UDP


def make_nlopt(solver, maxeval=2000):
    uda = pg.nlopt(solver)
    uda.maxeval = maxeval
    uda.xtol_rel = 1e-8
    uda.ftol_rel = 1e-8
    return pg.algorithm(uda)


def run_sade(udp, gen, pop_size, runs=1, seed_base=0):
    """sade 多次 runs; 算法与种群均固定种子 → 逐位可复现.
    (mp 脚本未给算法种子, pygmo 默认随机, 故其每次运行都不同;
     orbcalc 固定种子保证同配置同结果.)"""
    best = None
    for r in range(runs):
        algo = pg.algorithm(pg.sade(gen=gen, ftol=1e-6, xtol=1e-6,
                                    seed=seed_base + r + 100000))
        pop = pg.population(pg.problem(udp), size=pop_size, seed=seed_base + r)
        pop = algo.evolve(pop)
        f, x = pop.champion_f[0], list(pop.champion_x)
        if best is None or f < best[0]:
            best = (f, x)
    return best


def local_refine(udp, x, algo_name="sbplx", iters=2000):
    prob = pg.problem(udp)
    lb, ub = prob.get_bounds()
    x0 = np.clip(np.array(x), lb, ub)
    if algo_name in ("sbplx", "cobyla"):
        a = make_nlopt(algo_name, maxeval=iters)
    elif algo_name == "compass":
        a = pg.algorithm(pg.compass_search(max_fevals=iters, start_range=0.1,
                                           seed=20240801))
    else:
        a = pg.algorithm(pg.xnes(gen=200, seed=20240801))
    pop = pg.population(prob, size=1)
    pop.set_x(0, x0)
    pop = a.evolve(pop)
    return pop.champion_f[0], list(pop.champion_x)


def narrow_tof_box(x_ref, cfg, pct=0.10, n_legs=None):
    """以参考解为中心 ±pct 的 TOF 盒 (不越全局边界)."""
    n_legs = n_legs or (len(cfg.seq) - 1)
    ntof = []
    for (lo, hi), t in zip(cfg.tof_bounds,
                           [float(x_ref[5 + 4 * i]) for i in range(n_legs)]):
        ntof.append([max(lo, t * (1 - pct)), min(hi, t * (1 + pct))])
    return ntof


# ---------------------------------------------------------------------------
# worker 任务 (必须模块级, Windows spawn 可 pickle)
# ---------------------------------------------------------------------------
def _build_udp(kind, cfg, t0, tof, w1, w2):
    if kind == "dsm":
        return DSM_UDP(cfg, t0=t0, tof=tof)
    return TOF_UDP(cfg, t0=t0, tof=tof, w1=w1, w2=w2)


def _run_sade_task(kind, cfg, t0, tof, w1, w2, gen, pop, seed):
    """worker: 单次 sade 运行 (一个随机种子)."""
    try:
        udp = _build_udp(kind, cfg, t0, tof, w1, w2)
        f, x = run_sade(udp, gen, pop, runs=1, seed_base=seed)
        return f, list(x)
    except Exception:
        import traceback
        slog.err(f"[worker sade] kind={kind} t0={t0} tof={tof} seed={seed} " +
                 f"gen={gen} pop={pop} cfg={cfg.name}\n"
                 f"{traceback.format_exc()}")
        return 1e18, []


def _sbplx_task(kind, cfg, t0, tof, w1, w2, x0, maxeval):
    """worker: 单起点 sbplx 局部搜索."""
    try:
        udp = _build_udp(kind, cfg, t0, tof, w1, w2)
        prob = pg.problem(udp)
        pop = pg.population(prob, size=1)
        pop.set_x(0, list(x0))
        pop = make_nlopt("sbplx", maxeval=maxeval).evolve(pop)
        return pop.champion_f[0], list(pop.champion_x)
    except Exception:
        import traceback
        slog.err(f"[worker sbplx] kind={kind} t0={t0} x0={x0} maxeval={maxeval} cfg={cfg.name}\n"
                 f"{traceback.format_exc()}")
        return 1e18, list(x0)


def multistart_mp(executor, kind, cfg, t0, tof, w1, w2, x_ref, n_seeds=60,
                  maxeval=1500, seed=7, pct=0.12):
    """窄盒内多起点局部搜索 (并行版): 种子 0 = 参考解本身, 保证单调不减.
    起点生成方式与串行版逐位一致 (同一 rng 序列), 各起点 sbplx 并行."""
    udp = _build_udp(kind, cfg, t0, tof, w1, w2)
    lb, ub = udp.get_bounds()
    rng = np.random.default_rng(seed)
    xr = np.clip(np.array(x_ref), lb, ub)
    span = np.array(ub) - np.array(lb)
    width = pct * np.abs(xr) + 0.02 * span + 1e-9
    lo = np.maximum(lb, xr - width)
    hi = np.minimum(ub, xr + width)
    lo = np.minimum(lo, hi - 1e-7 * span - 1e-9)
    lo[0], hi[0] = max(lb[0], xr[0] - 10.0), min(ub[0], xr[0] + 10.0)
    if lo[0] >= hi[0]:
        lo[0], hi[0] = lb[0], ub[0]
    starts = [xr] + [rng.uniform(lo, hi) for _ in range(n_seeds - 1)]
    futs = [executor.submit(_sbplx_task, kind, cfg, list(t0), tof, w1, w2,
                            list(s), maxeval) for s in starts]
    best = None
    for fut in futs:
        ff, xx = fut.result()
        if ff < 1e12 and (best is None or ff < best[0]):
            best = (ff, xx)
    return best