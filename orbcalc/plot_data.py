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

from .planets import get_planet

MJD_LO, MJD_HI = -55000.0, 55000.0
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
    for i in range(len(epochs) - 1):
        r0, v0 = blegs[2 * i]
        t_a, t_b = bep[2 * i], bep[2 * i + 1]
        dt_b = (t_b - t_a) * pk.DAY2SEC
        bal = _polyline([r0, v0], dt_b, mu, N=120) if dt_b > 0 else {"x": [], "y": [], "z": []}
        r_dsm, v_dsm = blegs[2 * i + 1]
        dt_l = (epochs[i + 1] - t_b) * pk.DAY2SEC
        lam = _polyline([r_dsm, v_dsm], dt_l, mu, N=120) if dt_l > 0 else {"x": [], "y": [], "z": []}
        legs.append({
            "from": cfg.seq[i], "to": cfg.seq[i + 1],
            "ballistic": bal, "lambert": lam,
            "dsm": {"x": float(r_dsm[0] / pk.AU), "y": float(r_dsm[1] / pk.AU),
                    "z": float(r_dsm[2] / pk.AU),
                    "iso": str(pk.epoch(t_b).to_datetime()),
                    "dsm_ms": round(info["dsm"][i], 2)},
        })

    return {
        "name": cfg.name,
        "units": "AU",
        "sun": {"x": [0.0], "y": [0.0], "z": [0.0]},
        "bodies": bodies,
        "legs": legs,
        "epochs_iso": [str(pk.epoch(e).to_datetime()) for e in epochs],
        "tof_yr": round(sum(info["tofs"]) / 365.25, 4),
        "dsm_total_ms": round(info["dsm_total"], 1),
    }


def render_png(cfg, info, path):
    """matplotlib Agg 静态图 (服务端渲染, 无交互窗口)."""
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    mu = pk.MU_SUN
    seq = cfg.seq
    epochs = info["epochs"]
    t_mid = 0.5 * (epochs[0] + epochs[-1])

    ax = pk.plot.make_3Daxis(figsize=(9, 8))
    pk.plot.add_sun(ax, s=80)

    for idx, tag in enumerate(seq):
        pla = get_planet(tag)
        if tag in _INNER:
            half = PERIOD.get(tag, 365.25) / 2.0
            _plot_arc(ax, pla, t_mid - half, t_mid + half,
                      c=BODY_COLOR.get(tag, "gray"), alpha=0.7, lw=1.2)
        else:
            _plot_arc(ax, pla, epochs[0], epochs[-1],
                      c=BODY_COLOR.get(tag, "gray"), alpha=0.7, lw=1.2)

    blegs, bep = info["blegs"], info["bep"]
    for i in range(len(epochs) - 1):
        r0, v0 = blegs[2 * i]
        t_a, t_b = bep[2 * i], bep[2 * i + 1]
        dt_b = (t_b - t_a) * pk.DAY2SEC
        if dt_b > 0:
            _plot_polyline(ax, [r0, v0], dt_b, mu, c="pink", lw=1.8, alpha=0.9)
        r_dsm, v_dsm = blegs[2 * i + 1]
        dt_l = (epochs[i + 1] - t_b) * pk.DAY2SEC
        if dt_l > 0:
            _plot_polyline(ax, [r_dsm, v_dsm], dt_l, mu, c="pink", lw=1.8, alpha=0.9)
        ax.scatter([r_dsm[0] / pk.AU], [r_dsm[1] / pk.AU], [r_dsm[2] / pk.AU],
                   c="darkviolet", marker="*", s=28, zorder=5)

    try:
        for pla, e in zip([get_planet(t) for t in seq], [pk.epoch(v) for v in epochs]):
            pk.plot.add_planet(ax, pla, when=e, units=pk.AU, s=25, alpha=0.9)
    except Exception:
        pass

    ax.set_title(f"{cfg.name} MGA-1DSM (de440s)\n"
                 f"Launch {pk.epoch(epochs[0]).to_datetime().date()}  ·  "
                 f"TOF = {sum(info['tofs']) / 365.25:.2f} yr  ·  "
                 f"Total DSM = {info['dsm_total']:.0f} m/s", fontsize=11)
    ax.view_init(elev=90, azim=-90)
    lim = 22.0
    ax.set_xlim3d(-lim, lim); ax.set_ylim3d(-lim, lim); ax.set_zlim3d(-1.0, 1.0)
    ax.axis("off")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close(ax.figure)


def _plot_arc(ax, pla, t0, t1, **kw):
    a, b = _clamp(t0, t1)
    xs, ys, zs = [], [], []
    for e in np.linspace(a, b, 200):
        try:
            r, _ = pla.eph(pk.epoch(float(e)))
        except Exception:
            continue
        xs.append(r[0] / pk.AU); ys.append(r[1] / pk.AU); zs.append(r[2] / pk.AU)
    if len(xs) >= 2:
        ax.plot(xs, ys, zs, **kw)


def _plot_polyline(ax, rv0, dt_sec, mu, N=120, **kw):
    r0, v0 = rv0[0], rv0[1]
    xs, ys, zs = [], [], []
    for k in range(N + 1):
        r, v = pk.propagate_lagrangian([r0, v0], dt_sec * k / N, mu)
        xs.append(r[0] / pk.AU); ys.append(r[1] / pk.AU); zs.append(r[2] / pk.AU)
    ax.plot(xs, ys, zs, **kw)