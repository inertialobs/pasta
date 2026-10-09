# -*- coding: utf-8 -*-
"""
解码 / 文本报告 / 结构化摘要 — 与 temp/EVVEJU_TOF_1DSM_mp.py 的
decode/report 逐位一致, 报告文本改为 cfg 驱动; 另输出结构化 summarize()。
"""
from __future__ import annotations

import numpy as np
import pykep as pk

from .planets import planet_safe_radius


def _rtn_components(r, v, vec):
    """冲量向量在局部 R/T/N 系的分量 [m/s].

    R = 径向 (r), N = 轨道法向 (r x v), T = 横向 (N x R).
    无效几何 (|r|~0 或 r 与 v 平行) 时返回 [0, 0, 0]。
    """
    r = np.asarray(r, dtype=float)
    v = np.asarray(v, dtype=float)
    vec = np.asarray(vec, dtype=float)
    rn = float(np.linalg.norm(r))
    h = np.cross(r, v)
    hn = float(np.linalg.norm(h))
    if rn < 1e-9 or hn < 1e-12:
        return [0.0, 0.0, 0.0]
    r_hat = r / rn
    n_hat = h / hn
    t_hat = np.cross(n_hat, r_hat)
    return [float(np.dot(vec, r_hat)), float(np.dot(vec, t_hat)), float(np.dot(vec, n_hat))]


def _rpn_components(r, v, vec):
    """冲量在速度系 (KSP-radial / Prograde / Normal) 的分量 [m/s], 顺序 R,P,N.

    R = N×P (轨道面内垂直速度, KSP 机动节点 radial); P = v̂ (沿速度); N = 轨道法向 (r×v)。
    基 (P, R, N) 为右手系 (P×R=N); 此处按 R,P,N 输出以与 RTN 排列一致。
    """
    r = np.asarray(r, dtype=float)
    v = np.asarray(v, dtype=float)
    vec = np.asarray(vec, dtype=float)
    vn = float(np.linalg.norm(v))
    h = np.cross(r, v)
    hn = float(np.linalg.norm(h))
    if vn < 1e-12 or hn < 1e-12:
        return [0.0, 0.0, 0.0]
    p_hat = v / vn
    n_hat = h / hn
    r_hat = np.cross(n_hat, p_hat)
    return [float(np.dot(vec, r_hat)), float(np.dot(vec, p_hat)), float(np.dot(vec, n_hat))]


def dsm_vectors(blegs, bep, n, mu=None):
    """重建每腿 DSM 冲量向量 (m/s, 日心惯性系) 及其 R/T/N、P/R/N 分量.

    pykep 的 _compute_dvs 只返回 DSM ΔV 的模长; 这里用弹道腿起点按同一 mu
    传播到 DSM 历元恢复冲量前速度, 再取 (冲量后速度 - 冲量前速度)。
    模长应与 decode()['dsm'] 一致。
    """
    if mu is None:
        mu = pk.MU_SUN
    vecs, rtns, rpns = [], [], []
    for i in range(n):
        r0, v0 = blegs[2 * i]
        t_a, t_b = bep[2 * i], bep[2 * i + 1]
        r_dsm, v_post = blegs[2 * i + 1]
        dt = (t_b - t_a) * pk.DAY2SEC
        try:
            if dt > 0:
                _, v_pre = pk.propagate_lagrangian([r0, v0], dt, mu)
            else:
                v_pre = v0
        except Exception:
            v_pre = v0
        vec = [float(v_post[k] - v_pre[k]) for k in range(3)]
        vecs.append(vec)
        rtns.append(_rtn_components(list(r_dsm), list(v_pre), vec))
        rpns.append(_rpn_components(list(r_dsm), list(v_pre), vec))
    return vecs, rtns, rpns


def decode(x, udp):
    """解向量 -> 信息字典 (udp 为 pykep trajopt.mga_1dsm 对象)."""
    DV, lamberts, T, blegs, bep = udp._compute_dvs(list(x))
    n = udp.n_legs
    t0 = float(x[0])
    tofs = [float(x[5 + 4 * i]) for i in range(n)]
    epochs = [t0]
    for dt in tofs:
        epochs.append(epochs[-1] + dt)
    dsm = [float(v) for v in DV[:n]]
    rps = [float(x[7 + 4 * (i - 1)]) for i in range(1, n)]
    betas = [float(x[6 + 4 * (i - 1)]) for i in range(1, n)]
    etas = [float(x[4 + 4 * i]) for i in range(n)]
    vecs, rtns, rpns = dsm_vectors(blegs, bep, n)
    return dict(
        x=x, t0=t0, tofs=tofs, epochs=epochs, dsm=dsm,
        dsm_total=sum(dsm), vinf_launch=float(x[3]),
        vinf_arr=float(DV[-1]) if len(DV) > n else 0.0,
        rps=rps, betas=betas, etas=etas, T=T,
        blegs=blegs, bep=bep, lamberts=lamberts,
        dsm_vecs=vecs, dsm_rtn=rtns, dsm_rpn=rpns,
    )


def _leg_labels(cfg):
    seq = cfg.seq
    n = len(seq) - 1
    names = []
    counts = {}
    for tag in seq:
        counts[tag] = counts.get(tag, 0) + 1
        from .planets import TAG_SHORT
        sh = TAG_SHORT.get(tag, tag)
        names.append(f"{sh}{counts[tag]}" if counts[tag] > 1 else sh)
    return names


def report(info, cfg, title="ORBIT SOLUTION (orbcalc)"):
    """文本报告: 与 mp 脚本格式一致, 打印并返回文本 (由 run_cli 写日志)."""
    names = _leg_labels(cfg)
    total_d = sum(info["tofs"])
    line = "=" * 78
    out = [line, f"{title}: {'-'.join(cfg.seq)} (de440s)", line,
           f"Launch            : {pk.epoch(info['t0']).to_datetime()}"]
    for i, (nm, e) in enumerate(zip(names[1:], info["epochs"][1:])):
        out.append(f"  {nm:<8} @ {pk.epoch(e).to_datetime()}   TOF={info['tofs'][i]:>8.2f} d")
    out.append(f"Total TOF         : {total_d:.1f} d = {total_d / 365.25:.2f} yr")
    out.append("-" * 78)
    out.append(f"|v∞|_launch       : {info['vinf_launch'] / 1000:.4f} km/s")
    for i, d in enumerate(info["dsm"]):
        out.append(f"  DSM ΔV[{i}] ({names[i]}->{names[i+1]}) : {d / 1000:.4f} km/s  "
                   f"(eta={info['etas'][i]:.3f})")
    ok = info["dsm_total"] <= cfg.dsm_limit_ms
    out.append(f"Total DSM ΔV      : {info['dsm_total'] / 1000:.4f} km/s  = "
               f"{info['dsm_total']:.1f} m/s   "
               f"[{'DSM ≤ %.0f m/s ✓' % cfg.dsm_limit_ms if ok else 'DSM > %.0f m/s ✗' % cfg.dsm_limit_ms}]")
    out.append(f"|v∞|_arrival      : {info['vinf_arr'] / 1000:.4f} km/s")
    for i, nm in enumerate(names[1:-1]):
        tag = cfg.seq[i + 1]
        safe = planet_safe_radius(tag, cfg)
        rp = info["rps"][i]
        if safe is not None:
            # 以该行星自身半径换算地表高度 (需 REGISTRY 半径; 用 rp*R 与 safe_radius 比较)
            from .planets import get_planet
            R = get_planet(tag).radius
            alt_km = (rp * R - R) / 1000.0
            alt_ok = (rp * R) >= safe
            out.append(f"  Flyby {nm:<8} rp = {rp:.3f} R_planet = 地表以上 {alt_km:.1f} km "
                       f"(≥{(safe - R) / 1000.0:.0f} km {'✓' if alt_ok else '✗'})")
        else:
            out.append(f"  Flyby {nm:<8} rp = {rp:.3f} R_planet")
    out.append("-" * 78)
    txt = "\n".join(out)
    return txt


def summarize(info, cfg):
    """结构化摘要 -> result.json (网页结果卡所用)."""
    names = _leg_labels(cfg)
    total_d = sum(info["tofs"])
    legs = []
    rtn_all = info.get("dsm_rtn") or []
    rpn_all = info.get("dsm_rpn") or []
    bep = info.get("bep") or []
    for i in range(len(info["tofs"])):
        leg = {
            "from": names[i], "to": names[i + 1],
            "tof_d": round(info["tofs"][i], 2),
            "dsm_ms": round(info["dsm"][i], 2),
            "eta": round(info["etas"][i], 4),
        }
        if 2 * i + 1 < len(bep):
            leg["dsm_iso"] = str(pk.epoch(bep[2 * i + 1]).to_datetime())
            leg["dsm_elapsed_d"] = round(bep[2 * i + 1] - info["t0"], 3)
        if i < len(rtn_all):
            leg["dsm_rtn_ms"] = [round(float(c), 2) for c in rtn_all[i]]
        if i < len(rpn_all):
            leg["dsm_rpn_ms"] = [round(float(c), 2) for c in rpn_all[i]]
        legs.append(leg)
    flybys = []
    for i, nm in enumerate(names[1:-1]):
        tag = cfg.seq[i + 1]
        safe = planet_safe_radius(tag, cfg)
        rp = info["rps"][i]
        item = {"name": nm, "tag": tag, "rp_R": round(rp, 5), "beta": round(info["betas"][i], 4)}
        epochs = info.get("epochs") or []
        if i + 1 < len(epochs):
            item["arrive_iso"] = str(pk.epoch(epochs[i + 1]).to_datetime())
            item["arrive_elapsed_d"] = round(epochs[i + 1] - info["t0"], 3)
        if safe is not None:
            from .planets import get_planet
            R = get_planet(tag).radius
            item["alt_km"] = round((rp * R - R) / 1000.0, 1)
            item["min_alt_km"] = round((safe - R) / 1000.0, 1)
            item["alt_ok"] = bool((rp * R) >= safe)
        flybys.append(item)
    return {
        "name": cfg.name,
        "sequence": names,
        "tof_d": round(total_d, 1),
        "tof_yr": round(total_d / 365.25, 4),
        "dsm_total_ms": round(info["dsm_total"], 1),
        "dsm_ok": bool(info["dsm_total"] <= cfg.dsm_limit_ms),
        "dsm_limit_ms": cfg.dsm_limit_ms,
        "vinf_launch_kmps": round(info["vinf_launch"] / 1000.0, 4),
        "vinf_arrival_kmps": round(info["vinf_arr"] / 1000.0, 4),
        "launch_iso": str(pk.epoch(info["t0"]).to_datetime()),
        "arrival_iso": str(pk.epoch(info["epochs"][-1]).to_datetime()),
        "arrival_elapsed_d": round(info["epochs"][-1] - info["t0"], 3),
        "epochs_iso": [str(pk.epoch(e).to_datetime()) for e in info["epochs"]],
        "legs": legs,
        "flybys": flybys,
    }