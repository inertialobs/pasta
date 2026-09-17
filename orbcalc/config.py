# -*- coding: utf-8 -*-
"""
TrajConfig — 轨迹优化任务的完整配置 (JSON 可序列化, spawn 可 pickle)。

结构对齐 settings.Settings:
    - 继承 dict: 支持 cfg.seq 属性访问, 也支持 cfg["seq"] / dict 操作;
    - update(): 只接受已知字段, 先在候选副本上 validate, 通过后再提交;
    - DEFAULTS 为唯一字段清单与默认值, 每实例深拷贝 (可变默认值不共享)。

只负责"轨迹"配置; 计算/系统参数 (jobs、run_*、scan_keep_pct 等) 归 settings.py。
默认值与字段集集中定义在 orbcalc/__init__.py (TRAJ_DEFAULTS/TRAJ_FIELDS);
默认轨迹 (seq/eras/tof_bounds) 取自内置预设 presets/traj_evvejs_cassini.json。
"""
import copy
import json
import re
from pathlib import Path

from . import TRAJ_DEFAULTS, TRAJ_PRESET_KEYS
from .eras import EraSet
from .util import is_num

# 默认轨迹来源: 内置 Cassini 预设 (cwd 相对, 依赖启动时 chdir 到运行根)。
# 缺失/损坏即快速失败, 避免用残缺默认值静默跑出错误结果。
DEFAULT_TRAJ_FILE = Path("presets/traj_evvejs_cassini.json")


def _load_default_traj():
    try:
        raw = json.loads(DEFAULT_TRAJ_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuntimeError(f"默认轨迹预设不可读: {DEFAULT_TRAJ_FILE}: {e}") from e
    missing = [k for k in TRAJ_PRESET_KEYS if k not in raw]
    if missing:
        raise RuntimeError(f"{DEFAULT_TRAJ_FILE} 缺少默认轨迹字段: {missing}")
    return {k: raw[k] for k in TRAJ_PRESET_KEYS}


default_traj = _load_default_traj()


class TrajConfig(dict):
    def __init__(self):
        super().__init__()
        dict.update(self, copy.deepcopy({**TRAJ_DEFAULTS, **default_traj}))

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'TrajConfig' has no attribute '{name}'")

    def __setattr__(self, name, value):
        if name in self:
            self[name] = value
        else:
            raise AttributeError(f"'TrajConfig' has no attribute '{name}'")

    # ------------------------------------------------------------------
    def validate(self) -> bool:
        '''validate config'''
        n_legs = len(self.seq) - 1
        if n_legs < 1:
            raise ValueError("seq 至少需要 2 个天体")
        from .planets import REGISTRY
        unknown = [t for t in self.seq if t not in REGISTRY]
        if unknown:
            raise ValueError(f"seq 含未知行星 TAG: {unknown} (可用: {sorted(REGISTRY)})")
        if len(self.tof_bounds) != n_legs:
            raise ValueError(f"tof_bounds 应有 {n_legs} 条腿, 实际 {len(self.tof_bounds)}")
        for i, b in enumerate(self.tof_bounds):
            if (not isinstance(b, (list, tuple)) or len(b) != 2
                    or not is_num(b[0]) or not is_num(b[1])):
                raise ValueError(f"tof_bounds[{i}] 应为 [lo, hi] 数值, 实际 {b}")
            if b[0] > b[1]:
                raise ValueError(f"tof_bounds[{i}] lo > hi, 实际 {b}")
        if not (is_num(self.rp_ub) and self.rp_ub > 0):
            raise ValueError(f"rp_ub 应为正数, 实际 {self.rp_ub}")
        if (not isinstance(self.vinf_bounds_kmps, (list, tuple))
                or len(self.vinf_bounds_kmps) != 2
                or not all(is_num(v) for v in self.vinf_bounds_kmps)):
            raise ValueError(f"vinf_bounds_kmps 应为 [lo, hi] 两个数值, 实际 {self.vinf_bounds_kmps}")
        if self.vinf_bounds_kmps[0] > self.vinf_bounds_kmps[1]:
            raise ValueError(f"vinf_bounds_kmps lo > hi, 实际 {self.vinf_bounds_kmps}")
        if (not isinstance(self.eta_bounds, (list, tuple)) or len(self.eta_bounds) != 2
                or not all(is_num(v) for v in self.eta_bounds)
                or not (0 < self.eta_bounds[0] <= self.eta_bounds[1] <= 1)):
            raise ValueError(f"eta_bounds 应为 0 < lo <= hi <= 1, 实际 {self.eta_bounds}")
        for fname in ("penalty", "frontier_penalty"):
            v = self[fname]
            if (not isinstance(v, (list, tuple)) or len(v) != 2
                    or not all(is_num(x) and x >= 0 for x in v)):
                raise ValueError(f"{fname} 应为 [线性权, 二次权] 两个非负数值, 实际 {v}")
        for fname in ("dsm_limit_ms", "wl", "wa",
                      "vinf_launch_limit_ms", "vinf_arrival_limit_ms"):
            v = self[fname]
            if not (is_num(v) and v >= 0):
                raise ValueError(f"{fname} 应为非负数值, 实际 {v}")
        for k, v in self.safe_radius.items():
            if not (is_num(v) and v > 0):
                raise ValueError(f"safe_radius[{k}] 应为正数 (m), 实际 {v!r}")
        if self.objective not in ("min_tof", "min_dsm", "custom"):
            raise ValueError(f"objective 应为 min_tof/min_dsm/custom, 实际 {self.objective}")
        if self.objective == "custom" and len(self.objective_weights) != 2:
            raise ValueError(f"objective_weights 应为 [TOF权重, DSM权重], 实际 {self.objective_weights}")
        EraSet.from_dates(self.eras)   # 解析+校验 (格式/顺序/星历范围)
        return True

    # ------------------------------------------------------------------
    def update(self, d: dict):
        '''已知字段过滤 + 候选副本校验通过后再提交 (与 Settings.update 同构)'''
        patch = {k: v for k, v in (d or {}).items() if k in self}
        cand = TrajConfig()
        dict.update(cand, self)
        dict.update(cand, patch)
        cand.validate()
        dict.update(self, patch)

    @classmethod
    def from_dict(cls, d):
        cfg = cls()
        cfg.update(d or {})
        return cfg

    def to_json(self, path=None, indent=2):
        txt = json.dumps(dict(self), indent=indent, ensure_ascii=False)
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(txt)
        return txt

    @classmethod
    def from_json(cls, path=None, text=None):
        if text is None:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        return cls.from_dict(json.loads(text))

    @property
    def era_set(self) -> EraSet:
        """发射窗口集合 (解析/求交/窗口生成) — 供 stages 使用。"""
        return EraSet.from_dates(self.eras)


def sanitize_name(name):
    """任务名 -> 安全目录名."""
    s = re.sub(r"[^\w\-]+", "_", name).strip("_") or "job"
    return s[:40]
