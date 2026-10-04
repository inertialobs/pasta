# -*- coding: utf-8 -*-
"""
选优逻辑：从 decode 后的 info 生成排序键，与 cfg.objective 对齐。

把 select_key / candidate_key 独立出来，便于：
- 单测选优逻辑而不依赖 stages 完整流水线
- 未来多圈 / 多目标 / 前沿曲线等扩展
"""
from __future__ import annotations

from . import slog
from .decode_report import decode


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


def candidate_key(cfg, x, udp):
    """安全 decode 并返回 (select_key, info)；单个候选失败时返回最差键与 None。"""
    try:
        info = decode(x, udp.udp)
        return select_key(cfg, info), info
    except Exception as e:
        slog.wrn(f"[selection] decode failed for one candidate, treating as worst: {e}")
        return (1, 1e18, 1e18), None
