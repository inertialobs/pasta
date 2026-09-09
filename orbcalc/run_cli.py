# -*- coding: utf-8 -*-
"""
唯一计算入口 (供 web JobManager 以子进程调用, 也可独立命令行使用):

    python -m orbcalc.run_cli --config runs/<job>/config.json --jobs 8 --outdir runs/<job> [--debug]

行为与 temp/EVVEJU_TOF_1DSM_mp.py 主流程逐位一致 (cfg 驱动):
    [1] 扫描 -> [2] 细化 (run_scan)   | [3] 弹道播种 (run_seed, smoke 自动跳过)
    [w] 内置热启动 (warm_x 非空)      | [4] 宽 TOF 压缩 (run_compress)
    [5] 紧 TOF 压缩 (run_frontier)    | [6] pick_best -> 报告/汇总/绘图数据

日志: 统一走 orbcalc.slog (单写入者, [时间戳][级别] 标签+上下文)。
    绑定到 sys.stdout (web 子进程被 JobManager 重定向到 log.txt)。
    启动时 dump 环境 (版本/PID/OS/核数/配置摘要), 便于反馈定位。

产物 (outdir 内):
    log.txt        全程日志 (统一 slog 格式)
    best_x.npy     最优设计向量
    result.json    结构化摘要 (网页结果卡)
    plot.json      3D 图数据 (Plotly)
    trajectory.png 静态图 (Agg, 尽力而为)
退出码: 0 = 完成 (含"无可行解"); 2 = 流水线失败/异常。
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import platform
import sys
import time
import traceback

import numpy as np

from .config import TrajConfig
from .udp import TOF_UDP, DSM_UDP
from .decode_report import decode, report, summarize
from .plot_data import build_plot_json, render_png
from .stages import (phase_scan_mp, phase_refine_mp, phase_ballistic_seed_mp,
                     compress_pass_mp, pick_best)
from . import slog


def _force_utf8_stdio():
    """无论控制台/重定向目标是什么代码页, 统一 stdout/stderr 为 UTF-8\n    (报告文本含 ²/Δv 等字符, GBK 控制台会 UnicodeEncodeError)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _dump_env(cfg):
    slog.inf(f"[env] PASTA run_cli | pid={os.getpid()} | py={platform.python_version()} | "
             f"os={platform.platform()} | cpu_cores={os.cpu_count()}")
    slog.inf(f"[env] seq={cfg.seq} | n_legs={len(cfg.seq) - 1} | objective={cfg.objective} | "
             f"dsm_limit={cfg.dsm_limit_ms:.0f} m/s | vinf_bounds={cfg.vinf_bounds_kmps}")
    slog.inf(f"[env] eras={cfg.eras} | tof_bounds={cfg.tof_bounds} | rp_ub={cfg.rp_ub}")
    slog.inf(f"[env] jobs={cfg.jobs} | smoke={cfg.smoke} | scan_keep={cfg.scan_keep} | "
             f"refine_keep={cfg.refine_keep} | era_step_d={cfg.era_step_d}")
    slog.inf(f"[env] run: scan={cfg.run_scan} seed={cfg.run_seed} compress={cfg.run_compress} "
             f"frontier={cfg.run_frontier}")
    slog.inf(f"[env] warm_x={'yes' if cfg.warm_x is not None else 'no'} | penalty={cfg.penalty} | "
             f"frontier_penalty={cfg.frontier_penalty} | eta_bounds={cfg.eta_bounds}")


def run(args):
    cfg = TrajConfig.from_json(path=args.config)
    if args.jobs and args.jobs > 0:
        cfg.jobs = int(args.jobs)
    cfg.validate()

    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)
    _dump_env(cfg)

    t_start = time.time()
    summary = {"job": cfg.name, "status": "ok", "error": None}
    try:
        import pykep as pk
        import pygmo as pg
        slog.inf(f"[env] pykep {pk.__version__}  pygmo {pg.__version__}  numpy {np.__version__}")

        candidates = []
        from concurrent.futures import ProcessPoolExecutor
        from contextlib import nullcontext

        # 仅当存在并行阶段 (扫描/压缩) 时创建进程池, 纯评估任务零进程开销
        need_pool = cfg.run_scan or cfg.run_compress or cfg.run_frontier
        _ctx = ProcessPoolExecutor(max_workers=cfg.jobs) if need_pool else nullcontext(None)
        with _ctx as ex:
            if cfg.run_scan:
                slog.inf("[phase] [1/6] scan 开始")
                cands = phase_scan_mp(ex, cfg)
                slog.inf("[phase] [2/6] refine 开始")
                best_ref = phase_refine_mp(ex, cfg, cands)
                if best_ref is None:
                    slog.err("[main] refine failed (all windows sade failed)")
                    summary["status"] = "error"
                    summary["error"] = "refine failed (all windows sade failed)"
                    return 2
                f_ref, x_ref, info_ref, udp_ref = best_ref
                candidates.append((x_ref, udp_ref))
                if cfg.run_seed and not cfg.smoke:
                    slog.inf("[phase] [3/6] ballistic seed")
                    x_seed = phase_ballistic_seed_mp(ex, cfg, x_ref)
                    candidates.append((x_seed, DSM_UDP(cfg, t0=[x_seed[0] - 30, x_seed[0] + 30])))

            if cfg.warm_x is not None:
                try:
                    uw = TOF_UDP(cfg, t0=[cfg.warm_x[0] - 30, cfg.warm_x[0] + 30])
                    iw = decode(cfg.warm_x, uw.udp)
                    slog.inf(f"[warm] 内置热启动: TOF={sum(iw['tofs']):.0f} d "
                             f"({sum(iw['tofs']) / 365.25:.2f} yr)  DSM={iw['dsm_total']:.0f} m/s")
                    candidates.append((cfg.warm_x, uw))
                except Exception as e:
                    slog.wrn(f"[warm] skipped: {e}")

            if (cfg.run_compress or cfg.run_frontier) and candidates:
                def _key(item):
                    info = decode(item[0], item[1].udp)
                    return (0 if info["dsm_total"] <= cfg.dsm_limit_ms else 1,
                            sum(info["tofs"]))
                candidates.sort(key=_key)
                seeds = candidates[:2]
                if cfg.run_compress:
                    slog.inf("[phase] [4/6] wide compress")
                    for k, (sx, sudp) in enumerate(seeds):
                        xw = compress_pass_mp(ex, cfg, sx, f"4/6 compress wide #{k + 1}",
                                              cfg.penalty[0], cfg.penalty[1],
                                              smoke=cfg.smoke, pct=0.25)
                        candidates.append((xw, TOF_UDP(cfg, t0=[sx[0] - 30, sx[0] + 30])))
                if cfg.run_frontier:
                    slog.inf("[phase] [5/6] frontier tight compress")
                    bi, bx, budp = pick_best(cfg, candidates)
                    xf = compress_pass_mp(ex, cfg, bx, "5/6 frontier tight",
                                          cfg.frontier_penalty[0],
                                          cfg.frontier_penalty[1], smoke=cfg.smoke,
                                          pct=0.12)
                    candidates.append((xf, TOF_UDP(cfg, t0=[xf[0] - 30, xf[0] + 30])))

        if not candidates:
            slog.wrn("[main] no candidates")
            summary["status"] = "no_candidates"
            _write_artifacts(args, cfg, None, None, summary, t_start)
            return 0

        slog.inf("[phase] [6/6] pick_best")
        bi, bx, budp = pick_best(cfg, candidates)
        title = f"*** BEST {cfg.name} SOLUTION ***"
        slog.inf("\n" + report(bi, cfg, title) + "\n")
        summary["warm_used"] = cfg.warm_x is not None
        _write_artifacts(args, cfg, bi, bx, summary, t_start)
        slog.inf(f"[main] done in {time.time() - t_start:.1f} s")
        return 0
    except KeyboardInterrupt:
        slog.wrn("[main] interrupted")
        summary["status"] = "cancelled"
        _write_result(args, summary)
        return 130
    except Exception:
        summary["status"] = "error"
        summary["error"] = traceback.format_exc()
        slog.err("[main] error:\n" + summary["error"])
        _write_result(args, summary)
        return 2
    finally:
        # 兜底: 异常/退出时释放所有 multiprocessing 子进程 (防孤儿池 worker)。
        try:
            for _ch in multiprocessing.active_children():
                slog.dbg(f"[main] terminating stray worker pid={_ch.pid}")
                _ch.terminate()
        except Exception:
            pass


def _write_artifacts(args, cfg, info, bx, summary, t_start):
    if info is not None:
        summary.update(summarize(info, cfg))
        summary["elapsed_s"] = round(time.time() - t_start, 1)
        np.save(os.path.join(args.outdir, "best_x.npy"), np.array(bx))
        with open(os.path.join(args.outdir, "plot.json"), "w", encoding="utf-8") as f:
            json.dump(build_plot_json(cfg, info), f, ensure_ascii=False)
        try:
            render_png(cfg, info, os.path.join(args.outdir, "trajectory.png"))
            slog.inf(f"[artifacts] png -> {os.path.join(args.outdir, 'trajectory.png')}")
        except Exception as e:
            slog.wrn(f"[artifacts] png skipped: {e}")
    summary["elapsed_s"] = round(time.time() - t_start, 1)
    _write_result(args, summary)


def _write_result(args, summary):
    with open(os.path.join(args.outdir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False)
    slog.inf(f"[result] -> {os.path.join(args.outdir, 'result.json')}")


def main():
    _force_utf8_stdio()
    multiprocessing.freeze_support()
    ap = argparse.ArgumentParser(description="orbcalc 计算入口 (web 子进程 / CLI)")
    ap.add_argument("--config", required=True, help="TrajConfig JSON 路径")
    ap.add_argument("--jobs", type=int, default=0, help="覆盖 cfg.jobs (0 = 用配置值)")
    ap.add_argument("--outdir", required=True, help="产物目录 (log/result/plot/best_x)")
    ap.add_argument("--debug", action="store_true", help="DEBUG 级别日志")
    args = ap.parse_args()
    slog.setup(stream=sys.stdout, debug=args.debug)
    sys.exit(run(args))


if __name__ == "__main__":
    main()
