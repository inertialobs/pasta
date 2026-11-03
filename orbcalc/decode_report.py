# -*- coding: utf-8 -*-
"""
解码 / 文本报告 / 结构化摘要 — 与 temp/EVVEJU_TOF_1DSM_mp.py 的
decode/report 逐位一致, 报告文本改为 cfg 驱动; 另输出结构化 summarize()。
"""
from __future__ import annotations

import math

import numpy as np
import pykep as pk

from .planets import get_planet, planet_safe_radius


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


def orbital_elements(r0, v0, mu=pk.MU_SUN):
    """状态矢量 (m, m/s) -> 经典六根数; a [AU], 角度 [deg]。"""
    try:
        a, e, inc, raan, argp, nu = pk.ic2par(
            [list(map(float, r0)), list(map(float, v0))], mu)
    except Exception:
        return {"a_au": None, "e": None, "i_deg": None,
                "raan_deg": None, "argp_deg": None, "nu_deg": None}
    return {
        "a_au": round(float(a) / pk.AU, 6),
        "e": round(float(e), 6),
        "i_deg": round(math.degrees(float(inc)), 3),
        "raan_deg": round(math.degrees(float(raan)) % 360.0, 3),
        "argp_deg": round(math.degrees(float(argp)) % 360.0, 3),
        "nu_deg": round(math.degrees(float(nu)) % 360.0, 3),
    }


def _vec_kmps(v):
    return [round(float(c) / 1000.0, 6) for c in v]


def leg_details(info, cfg, planet_getter=None):
    """每腿两段轨道根数 + 每次出发的出射矢量 + 最终到达入射 v∞。"""
    from .mga import lambert_revs

    planet_getter = planet_getter or get_planet
    names = _leg_labels(cfg)
    blegs = info.get("blegs") or []
    bep = info.get("bep") or []
    tofs = info.get("tofs") or []
    types = list(getattr(cfg, "lambert_types", []) or [])
    legs = []
    for i in range(len(tofs)):
        if 2 * i + 1 >= len(blegs) or 2 * i + 1 >= len(bep):
            break
        r_dep, v_dep = blegs[2 * i]
        r_dsm, v_lam = blegs[2 * i + 1]
        t_dep, t_dsm = bep[2 * i], bep[2 * i + 1]
        _, v_pl = planet_getter(cfg.seq[i]).eph(pk.epoch(t_dep))
        vinf_out = [float(v_dep[j]) - float(v_pl[j]) for j in range(3)]
        rev = lambert_revs(types[i]) if i < len(types) else 0
        legs.append({
            "index": i, "from": names[i], "to": names[i + 1],
            "orbits": {
                "ballistic": {**orbital_elements(r_dep, v_dep),
                              "epoch_iso": str(pk.epoch(t_dep).to_datetime())},
                "lambert": {**orbital_elements(r_dsm, v_lam),
                            "epoch_iso": str(pk.epoch(t_dsm).to_datetime()),
                            "rev": int(rev)},
            },
            "departure": {
                "body": names[i],
                "epoch_iso": str(pk.epoch(t_dep).to_datetime()),
                "v_out_kmps": _vec_kmps(v_dep),
                "vinf_out_kmps": _vec_kmps(vinf_out),
                "vinf_out_mag_kmps": round(float(np.linalg.norm(vinf_out)) / 1000.0, 6),
            },
        })
    arrival = None
    lamberts = info.get("lamberts") or []
    epochs = info.get("epochs") or []
    if lamberts and epochs and tofs and len(types) >= len(tofs):
        last = len(tofs) - 1
        try:
            v_arr = lamberts[last].v1[types[last]]
            t_arr = epochs[-1]
            _, v_pl = planet_getter(cfg.seq[-1]).eph(pk.epoch(t_arr))
            vinf_in = [float(v_arr[j]) - float(v_pl[j]) for j in range(3)]
            arrival = {
                "body": names[-1],
                "epoch_iso": str(pk.epoch(t_arr).to_datetime()),
                "vinf_in_kmps": _vec_kmps(vinf_in),
                "vinf_in_mag_kmps": round(float(np.linalg.norm(vinf_in)) / 1000.0, 6),
            }
        except (AttributeError, IndexError, TypeError, ValueError):
            arrival = None
    return {"legs": legs, "arrival": arrival}


def _fmt_vec(v):
    return ", ".join(f"{float(x):.4f}" for x in v)


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
    details = leg_details(info, cfg)
    if details["legs"]:
        out.append("-" * 78)
        out.append("LEG ORBITS / DEPARTURES (heliocentric ecliptic J2000)")
        for d in details["legs"]:
            out.append(f"  Leg {d['index']} {d['from']}->{d['to']}")
            for key, label in (("ballistic", "departure->DSM"),
                               ("lambert", "DSM->arrival")):
                e = d["orbits"][key]
                if e["a_au"] is None:
                    out.append(f"    {label:<18} (无法计算)")
                    continue
                rev = f"  rev={e['rev']}" if "rev" in e else ""
                out.append(
                    f"    {label:<18} a={e['a_au']:.6f} AU  e={e['e']:.6f}  "
                    f"i={e['i_deg']:.3f}°  Ω={e['raan_deg']:.3f}°  "
                    f"ω={e['argp_deg']:.3f}°  ν={e['nu_deg']:.3f}°{rev}")
            dep = d["departure"]
            out.append(f"    departure {dep['body']:<8} "
                       f"v_out=({_fmt_vec(dep['v_out_kmps'])}) km/s  "
                       f"v∞=({_fmt_vec(dep['vinf_out_kmps'])}) km/s  "
                       f"|v∞|={dep['vinf_out_mag_kmps']:.4f} km/s")
        if details["arrival"]:
            a = details["arrival"]
            out.append(f"  Arrival {a['body']:<8} "
                       f"v∞=({_fmt_vec(a['vinf_in_kmps'])}) km/s  "
                       f"|v∞|={a['vinf_in_mag_kmps']:.4f} km/s")
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
    details = leg_details(info, cfg)
    by_index = {d["index"]: d for d in details["legs"]}
    for i in range(len(info["tofs"])):
        leg = {
            "from": names[i], "to": names[i + 1],
            "tof_d": round(info["tofs"][i], 2),
            "dsm_ms": round(info["dsm"][i], 2),
            "eta": round(info["etas"][i], 4),
            "lambert_type": int(cfg.lambert_types[i]),   # 0 -> 1/2, 1 -> 3, 2 -> 4
        }
        if 2 * i + 1 < len(bep):
            leg["dsm_iso"] = str(pk.epoch(bep[2 * i + 1]).to_datetime())
            leg["dsm_elapsed_d"] = round(bep[2 * i + 1] - info["t0"], 3)
        if i < len(rtn_all):
            leg["dsm_rtn_ms"] = [round(float(c), 2) for c in rtn_all[i]]
        if i < len(rpn_all):
            leg["dsm_rpn_ms"] = [round(float(c), 2) for c in rpn_all[i]]
        if i in by_index:
            leg["orbits"] = by_index[i]["orbits"]
            leg["departure"] = by_index[i]["departure"]
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
    result = {
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
    if details["arrival"]:
        result["arrival"] = details["arrival"]
    return result