# -*- coding: utf-8 -*-
"""
结果 -> 可视化数据:
  * build_plot_json(cfg, info) : Plotly 3D 所需的纯 JSON 结构
    (太阳 / 行星轨道弧 / 弹道+Lambert 双弧 / DSM 星标 / 交会历元)
  * render_png(cfg, info, path) : matplotlib Agg 静态图 (服务端, 无 plt.show)
两者复刻 temp/EVVEJU_TOF_1DSM_mp.py make_plot 的几何生成逻辑。
"""
from __future__ import annotations

import numpy as np
import pykep as pk

from . import MJD2000_MAX, MJD2000_MIN
from .decode_report import leg_details
from .planets import get_planet

MJD_LO, MJD_HI = MJD2000_MIN, MJD2000_MAX
DSM_ARROW_AU = 0.3
PERIOD = {
    "MERCURY": 88.0, "VENUS": 224.7, "EARTH": 365.25, "MARS": 687.0,
    "JUPITER": 4332.6, "SATURN": 10759.2, "URANUS": 30687.2, "NEPTUNE": 60190.0,
}
BODY_COLOR = {
    "EARTH": "royalblue", "VENUS": "gold", "JUPITER": "orange",
    "URANUS": "teal", "MERCURY": "gray", "MARS": "red",
    "SATURN": "khaki", "NEPTUNE": "navy",
}
_INNER = {"MERCURY", "VENUS", "EARTH", "MARS"}


def _clamp(a, b):
    a = max(float(a), MJD_LO)
    b = min(float(b), MJD_HI)
    if b <= a:
        a, b = MJD_LO, min(MJD_LO + 365.0, MJD_HI)
    return a, b


def _unit(vec):
    """单位向量; 零向量返回 None."""
    v = np.asarray(vec, dtype=float)
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        return None
    return [float(v[0] / n), float(v[1] / n), float(v[2] / n)]


def _arc_points(pla, t0, t1, N=200, units=None):
    units = units or pk.AU
    a, b = _clamp(t0, t1)
    xs, ys, zs, ts = [], [], [], []
    for e in np.linspace(a, b, int(N)):
        try:
            r, _ = pla.eph(pk.epoch(float(e)))
        except Exception:
            continue
        xs.append(float(r[0] / units)); ys.append(float(r[1] / units))
        zs.append(float(r[2] / units)); ts.append(float(e))
    return {"x": xs, "y": ys, "z": zs, "t": ts}


def _one_period(pla, t_center, period_days, N=360, units=None):
    return _arc_points(pla, t_center - period_days / 2.0, t_center + period_days / 2.0, N, units)


def _polyline(rv0, dt_sec, mu, N=120):
    r0, v0 = rv0[0], rv0[1]
    xs, ys, zs, ts = [], [], [], []
    for k in range(N + 1):
        r, v = pk.propagate_lagrangian([r0, v0], dt_sec * k / N, mu)
        xs.append(float(r[0] / pk.AU)); ys.append(float(r[1] / pk.AU))
        zs.append(float(r[2] / pk.AU))
        ts.append(float(k) / N)
    return {"x": xs, "y": ys, "z": zs}


def build_plot_json(cfg, info):
    """返回可 json.dumps 的 3D 图数据."""
    mu = pk.MU_SUN
    seq = cfg.seq
    epochs = info["epochs"]
    t_mid = 0.5 * (epochs[0] + epochs[-1])
    details = leg_details(info, cfg, planet_getter=get_planet)
    by_index = {d["index"]: d for d in details["legs"]}

    bodies = []
    seen = {}
    for idx, tag in enumerate(seq):
        pla = get_planet(tag)
        # 交会点 (每次出现都算坐标, 重复天体不再置 None)
        try:
            r, _ = pla.eph(pk.epoch(epochs[idx]))
            ex, ey, ez = float(r[0] / pk.AU), float(r[1] / pk.AU), float(r[2] / pk.AU)
        except Exception:
            ex = ey = ez = None
        encounter = {"iso": str(pk.epoch(epochs[idx]).to_datetime()),
                     "x": ex, "y": ey, "z": ez}
        if tag in seen:
            bodies[seen[tag]]["encounters"].append(encounter)
            continue
        if tag in _INNER:
            arc = _one_period(pla, t_mid, PERIOD.get(tag, 365.25), N=360)
        else:
            span = max(epochs[-1] - epochs[0], 30.0)
            arc = _arc_points(pla, epochs[0], epochs[-1], N=max(120, int(span / 40.0)))
        bodies.append({
            "tag": tag, "color": BODY_COLOR.get(tag, "gray"),
            "orbit": arc,
            "encounters": [encounter],
        })
        seen[tag] = len(bodies) - 1

    legs = []
    blegs, bep = info["blegs"], info["bep"]
    vecs_all = info.get("dsm_vecs") or []
    rtn_all = info.get("dsm_rtn") or []
    rpn_all = info.get("dsm_rpn") or []
    etas_all = info.get("etas") or []
    for i in range(len(epochs) - 1):
        r0, v0 = blegs[2 * i]
        t_a, t_b = bep[2 * i], bep[2 * i + 1]
        dt_b = (t_b - t_a) * pk.DAY2SEC
        bal = _polyline([r0, v0], dt_b, mu, N=120) if dt_b > 0 else {"x": [], "y": [], "z": []}
        r_dsm, v_dsm = blegs[2 * i + 1]
        dt_l = (epochs[i + 1] - t_b) * pk.DAY2SEC
        lam = _polyline([r_dsm, v_dsm], dt_l, mu, N=120) if dt_l > 0 else {"x": [], "y": [], "z": []}
        dsm = {"x": float(r_dsm[0] / pk.AU), "y": float(r_dsm[1] / pk.AU),
               "z": float(r_dsm[2] / pk.AU),
               "iso": str(pk.epoch(t_b).to_datetime()),
               "dsm_ms": round(info["dsm"][i], 2)}
        if i < len(etas_all):
            dsm["eta"] = round(float(etas_all[i]), 4)
        if i < len(vecs_all):
            dsm["vec"] = [float(c) for c in vecs_all[i]]
            dsm["unit"] = _unit(vecs_all[i])
        if i < len(rtn_all):
            dsm["rtn"] = [float(c) for c in rtn_all[i]]
        if i < len(rpn_all):
            dsm["rpn"] = [float(c) for c in rpn_all[i]]
        leg_data = {
            "from": cfg.seq[i], "to": cfg.seq[i + 1],
            "ballistic": bal, "lambert": lam,
            "dsm": dsm,
        }
        if i in by_index:
            leg_data["orbits"] = by_index[i]["orbits"]
            leg_data["departure"] = by_index[i]["departure"]
        legs.append(leg_data)

    vinf_launch = info.get("vinf_launch")
    result = {
        "name": cfg.name,
        "units": "AU",
        "sun": {"x": [0.0], "y": [0.0], "z": [0.0]},
        "bodies": bodies,
        "legs": legs,
        "epochs_iso": [str(pk.epoch(e).to_datetime()) for e in epochs],
        "tof_yr": round(sum(info["tofs"]) / 365.25, 4),
        "dsm_total_ms": round(info["dsm_total"], 1),
        "c3": round((vinf_launch / 1000.0) ** 2, 3) if vinf_launch is not None else None,
        "dsm_arrow_au": DSM_ARROW_AU,
    }
    if details["arrival"]:
        result["arrival"] = details["arrival"]
    return result


def _extend_bounds(points, xs, ys, zs):
    points.extend(zip(xs, ys, zs))


def _fit_3d_limits(ax, points):
    """按实际绘制几何自动取景, 让任务主体轨道尽量占满画布."""
    if not points:
        return
    xs, ys, zs = zip(*points)
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    zmin, zmax = min(zs), max(zs)
    cx = 0.5 * (xmin + xmax)
    cy = 0.5 * (ymin + ymax)
    cz = 0.5 * (zmin + zmax)
    radius = max(xmax - xmin, ymax - ymin, 1.0) * 0.5 * 1.12
    zradius = max((zmax - zmin) * 0.5 * 1.2, radius * 0.15, 0.25)
    ax.set_xlim3d(cx - radius, cx + radius)
    ax.set_ylim3d(cy - radius, cy + radius)
    ax.set_zlim3d(cz - zradius, cz + zradius)
    # 俯视图下压缩 z 方向的盒体比例, 让 x-y 主体轨道占满画布
    ax.set_proj_type("ortho")
    ax.set_box_aspect((1.0, 1.0, max(0.12, min(1.0, zradius / radius))), zoom=1.35)


def render_png_from_plot(plot, path):
    """由 plot.json 结构渲染静态 PNG (与网页 3D 图使用同一份几何)."""
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    ax = pk.plot.make_3Daxis(figsize=(9, 8))
    pk.plot.add_sun(ax, s=80)
    points = [(0.0, 0.0, 0.0)]

    for body in plot.get("bodies") or []:
        color = body.get("color", "gray")
        orbit = body.get("orbit") or {}
        xs, ys, zs = orbit.get("x") or [], orbit.get("y") or [], orbit.get("z") or []
        if len(xs) >= 2:
            ax.plot(xs, ys, zs, color=color, alpha=0.7, lw=1.2)
            _extend_bounds(points, xs, ys, zs)
        for enc in body.get("encounters") or []:
            ex, ey, ez = enc.get("x"), enc.get("y"), enc.get("z")
            if ex is None or ey is None or ez is None:
                continue
            ax.scatter([ex], [ey], [ez], c=color, s=25, alpha=0.9, zorder=4)
            points.append((ex, ey, ez))

    arrow_au = plot.get("dsm_arrow_au") or DSM_ARROW_AU
    for leg in plot.get("legs") or []:
        for key in ("ballistic", "lambert"):
            seg = leg.get(key) or {}
            xs, ys, zs = seg.get("x") or [], seg.get("y") or [], seg.get("z") or []
            if len(xs) >= 2:
                ax.plot(xs, ys, zs, color="pink", alpha=0.9, lw=1.8)
                _extend_bounds(points, xs, ys, zs)
        dsm = leg.get("dsm") or {}
        dx, dy, dz = dsm.get("x"), dsm.get("y"), dsm.get("z")
        if dx is None or dy is None or dz is None:
            continue
        ax.scatter([dx], [dy], [dz], c="darkviolet", marker="*", s=28, zorder=5)
        points.append((dx, dy, dz))
        unit = dsm.get("unit")
        if isinstance(unit, list) and len(unit) == 3:
            ax.quiver([dx], [dy], [dz], [unit[0]], [unit[1]], [unit[2]],
                      length=arrow_au, normalize=True, color="crimson",
                      arrow_length_ratio=0.25, lw=1.2, zorder=6)

    name = plot.get("name") or "MGA-1DSM"
    epochs_iso = plot.get("epochs_iso") or []
    launch = str(epochs_iso[0])[:10] if epochs_iso else ""
    title = f"{name} MGA-1DSM (de440s)\n"
    if launch:
        title += f"Launch {launch}  ·  "
    title += (f"TOF = {float(plot.get('tof_yr') or 0.0):.2f} yr  ·  "
              f"Total DSM = {float(plot.get('dsm_total_ms') or 0.0):.0f} m/s")
    ax.set_title(title, fontsize=11)
    ax.view_init(elev=90, azim=-90)
    _fit_3d_limits(ax, points)
    ax.axis("off")
    plt.tight_layout()
    ax.figure.subplots_adjust(left=0.01, right=0.99, bottom=0.01, top=0.88)
    plt.savefig(path, dpi=150, bbox_inches="tight", pad_inches=0.02)
    plt.close(ax.figure)


def render_png(cfg, info, path):
    """兼容入口: 从 cfg/info 构建 plot 数据后渲染 PNG."""
    render_png_from_plot(build_plot_json(cfg, info), path)