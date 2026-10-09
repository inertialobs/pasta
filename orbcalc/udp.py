# -*- coding: utf-8 -*-
"""
UDP (User Defined Problem) 封装 — 数值逻辑与 temp/EVVEJU_TOF_1DSM_mp.py 逐位一致,
仅把硬编码常量替换为 cfg (TrajConfig) 字段。

- TOF_UDP : 目标 = 总飞行时间 + DSM>limit 罚 + v∞ 边界罚 (主目标, 网页默认)
- DSM_UDP : 目标 = 总 DSM + v∞ 罚 (弹道播种 / 前端"min_dsm"模式)
"""
from __future__ import annotations

import numpy as np
import pykep as pk
import pygmo as pg

from .mga import MGA1DSM
from .planets import build_seq


def _make_trajopt(cfg, t0, tof, vinf, add_vinf_dep=False, add_vinf_arr=True):
    seq, _ = build_seq(cfg)
    if t0 is None:
        # decode-only 场景: 用 era 全跨度作为占位边界, 不影响 _compute_dvs
        era = cfg.era_set.ranges
        t0 = [era[0][0], era[-1][1]]
    return MGA1DSM(
        seq=seq, tof_encoding="direct",
        t0=[float(t0[0]), float(t0[1])],
        tof=tof if tof is not None else [list(b) for b in cfg.tof_bounds],
        vinf=vinf,
        add_vinf_dep=add_vinf_dep, add_vinf_arr=add_vinf_arr,
        multi_objective=False, orbit_insertion=False,
        eta_bounds=cfg.eta_bounds, rp_ub=cfg.rp_ub,   # pykep mga_1dsm 仅支持标量
        lambert_types=cfg.lambert_types,
    )


class TOF_UDP:
    def __init__(self, cfg, t0=None, tof=None, w1=None, w2=None):
        self.cfg = cfg
        if t0 is None:
            era = cfg.era_set.ranges
            t0 = [era[0][0], era[-1][1]]
        self.t0 = [float(t0[0]), float(t0[1])]
        self.tof = tof
        self.w1 = cfg.penalty[0] if w1 is None else w1
        self.w2 = cfg.penalty[1] if w2 is None else w2
        # 目标权重: min_tof -> [1,0]; min_dsm -> [0,1]; custom -> objective_weights
        if cfg.objective == "min_tof":
            self.w_tof, self.w_dsm = 1.0, 0.0
        elif cfg.objective == "min_dsm":
            self.w_tof, self.w_dsm = 0.0, 1.0
        else:
            self.w_tof, self.w_dsm = float(cfg.objective_weights[0]), float(cfg.objective_weights[1])
        self.udp = _make_trajopt(cfg, t0, tof, cfg.vinf_bounds_kmps)
        self._bounds = None

    def get_bounds(self):
        if self._bounds is None:
            self._bounds = pg.problem(self.udp).get_bounds()
        return self._bounds

    def fitness(self, x):
        try:
            DV, _, _, _, _ = self.udp._compute_dvs(list(x))
        except Exception:
            return [1e12]
        n = self.udp.n_legs
        tof = float(sum(x[5 + 4 * i] for i in range(n)))
        dsm = float(sum(DV[:n]))
        vinf_l = float(x[3])
        vinf_a = float(DV[-1]) if len(DV) > n else 0.0
        if not (np.isfinite(dsm) and np.isfinite(vinf_l) and np.isfinite(vinf_a)):
            return [1e12]                       # Lambert 不可行 -> 巨大惩罚
        pen_dsm = self.w1 * max(0.0, dsm - self.cfg.dsm_limit_ms) \
                  + self.w2 * max(0.0, dsm - self.cfg.dsm_limit_ms) ** 2
        pen = pen_dsm \
              + self.cfg.wl * max(0.0, vinf_l - self.cfg.vinf_launch_limit_ms) ** 2 \
              + self.cfg.wa * max(0.0, vinf_a - self.cfg.vinf_arrival_limit_ms) ** 2
        # 加权目标 (默认 [1,0] -> tof+pen, 与原行为逐位一致)
        return [self.w_tof * tof + self.w_dsm * dsm + pen]

    def get_nobj(self):
        return 1

    def get_nec(self):
        return 0

    def get_nic(self):
        return 0

    def get_name(self):
        return f"{self.cfg.name} TOF-1DSM (obj=w_tof*TOF+w_dsm*DSM, DSM<={self.cfg.dsm_limit_ms:.0f} m/s)"

    def get_extra_info(self):
        return (f"seq={self.cfg.name}, t0=[{self.t0[0]:.1f},{self.t0[1]:.1f}], "
                f"w=({self.w_tof},{self.w_dsm}), DSM-penalty(>{self.cfg.dsm_limit_ms:.0f} m/s)")


class DSM_UDP:
    """弹道播种用: 目标 = 总 DSM (用于生成低 DSM 种子解 / 前端 min_dsm 模式)."""

    def __init__(self, cfg, t0=None, tof=None):
        self.cfg = cfg
        if t0 is None:
            era = cfg.era_set.ranges
            t0 = [era[0][0], era[-1][1]]
        self.t0 = [float(t0[0]), float(t0[1])]
        self.tof = tof
        self.udp = _make_trajopt(cfg, t0, tof, cfg.vinf_bounds_kmps)
        self._bounds = None

    def get_bounds(self):
        if self._bounds is None:
            self._bounds = pg.problem(self.udp).get_bounds()
        return self._bounds

    def fitness(self, x):
        try:
            DV, _, _, _, _ = self.udp._compute_dvs(list(x))
        except Exception:
            return [1e12]
        n = self.udp.n_legs
        dsm = float(sum(DV[:n]))
        vinf_l = float(x[3])
        vinf_a = float(DV[-1]) if len(DV) > n else 0.0
        if not (np.isfinite(dsm) and np.isfinite(vinf_l) and np.isfinite(vinf_a)):
            return [1e12]
        return [dsm + self.cfg.wl * max(0.0, vinf_l - self.cfg.vinf_launch_limit_ms) ** 2
                + self.cfg.wa * max(0.0, vinf_a - self.cfg.vinf_arrival_limit_ms) ** 2]

    def get_nobj(self):
        return 1

    def get_nec(self):
        return 0

    def get_nic(self):
        return 0

    def get_name(self):
        return f"{self.cfg.name} DSM-min seed (ballistic warm start)"

    def get_extra_info(self):
        return "obj = total DSM + vinf penalties"