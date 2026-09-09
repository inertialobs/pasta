# -*- coding: utf-8 -*-
"""
TrajConfig — 轨迹优化任务的完整配置 (JSON 可序列化, spawn 可 pickle)。

默认值逐项对齐 temp/EVVEJU_TOF_1DSM_mp.py 的当前常量, 保证与 CLI 脚本数值等价。
"""
from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field, asdict

# ---------------------------------------------------------------------------
# 默认常量 (与 mp 脚本一致)
# ---------------------------------------------------------------------------
DEFAULT_SEQ = ["EARTH", "VENUS", "VENUS", "EARTH", "JUPITER", "URANUS"]

DEFAULT_TOF_BOUNDS = [
    [190.0, 200.0],     # Earth  -> Venus1 (Cassini ~197 d)
    [300.0, 500.0],     # Venus1 -> Venus2 (Cassini ~397 d)
    [10.0, 90.0],       # Venus2 -> Earth2 (Cassini ~50 d)
    [400.0, 1100.0],    # Earth2 -> Jupiter (收紧上界)
    [1200.0, 4500.0],   # Jupiter-> Uranus
]
DEFAULT_VINF_BOUNDS_KMPS = [3.5, 6.0]
DEFAULT_ETA_BOUNDS = [0.01, 0.9]
DEFAULT_RP_UB = 30.0

DEFAULT_DSM_LIMIT = 750.0                    # m/s (硬核验阈值)
DEFAULT_PENALTY = [10.0, 0.2]                # 默认 DSM 越界罚 (线性, 二次)
DEFAULT_FRONTIER_PENALTY = [30.0, 2.0]       # 前沿阶段更强罚
DEFAULT_WL, DEFAULT_VLF = 2e-5, 5000.0       # 发射 v∞ 超 5.0 km/s 罚 (m/s)
DEFAULT_WA, DEFAULT_VAF = 2e-5, 9000.0       # 到达 v∞ 超 9.0 km/s 罚 (m/s)

# 发射窗口时代 (与 mp 脚本一致)
DEFAULT_ERAS = [
    ["2029-01-01", "2033-06-30"],   # UOP (原 "uop")
    ["2017-01-01", "2021-01-01"],   # Cassini (原 "cassini")
]
DEFAULT_ERA_STEP_D = 60.0           # None → smoke?90 : 60 (与脚本一致)

# 内置热启动 (2026-08 已知最优: TOF=9.86 yr, DSM=750.0 m/s, 金星≥200km)
#   [t0, u, v, Vinf, eta1, T1, beta, rp, eta2, T2, beta, rp, eta3, T3,
#    beta, rp, eta4, T4, beta, rp, eta5, T5]
DEFAULT_WARM_X = [
    6790.673916, 0.741830, 0.596849, 4577.372480, 0.012963, 199.956541,
    -1.841368, 1.788768, 0.406503, 397.483336, -1.430037, 1.078390,
    0.180811, 58.108399, -1.201086, 2.544347, 0.605277, 643.699581,
    4.722208, 6.692400, 0.010289, 2303.322223,
]

# 行星安全半径覆盖 (m); 缺省看 planets.DEFAULT_SAFE_RADIUS
DEFAULT_SAFE_RADIUS = None           # 键: 行星 TAG -> 半径 m


@dataclass
class TrajConfig:
    # --- 基本 ---
    name: str = "EVVEJU"
    seq: list = field(default_factory=lambda: list(DEFAULT_SEQ))

    # --- 约束/边界 ---
    safe_radius: dict = field(default_factory=dict)   # TAG -> 半径 m (覆盖默认)
    tof_bounds: list = field(default_factory=lambda: [list(b) for b in DEFAULT_TOF_BOUNDS])
    vinf_bounds_kmps: list = field(default_factory=lambda: list(DEFAULT_VINF_BOUNDS_KMPS))
    eta_bounds: list = field(default_factory=lambda: list(DEFAULT_ETA_BOUNDS))
    rp_ub: float = DEFAULT_RP_UB    # 全局飞掠 rp 上界 (pykep mga_1dsm 仅支持标量)

    # --- 目标与罚函数 ---
    objective: str = "min_tof"        # "min_tof" | "min_dsm" | "custom"
    objective_weights: list = field(default_factory=lambda: [1.0, 0.0])  # custom: [TOF, DSM] 权重
    dsm_limit_ms: float = DEFAULT_DSM_LIMIT
    penalty: list = field(default_factory=lambda: list(DEFAULT_PENALTY))
    frontier_penalty: list = field(default_factory=lambda: list(DEFAULT_FRONTIER_PENALTY))
    wl: float = DEFAULT_WL
    vinf_launch_limit_ms: float = DEFAULT_VLF
    wa: float = DEFAULT_WA
    vinf_arrival_limit_ms: float = DEFAULT_VAF

    # --- 发射窗口时代 ---
    eras: list = field(default_factory=lambda: [list(e) for e in DEFAULT_ERAS])
    era_step_d: float | None = None   # None → smoke?90:60 (与脚本一致)

    # --- 并行 / 模式 ---
    jobs: int = 8
    smoke: bool = False
    scan_keep: int = 8    # 扫描阶段保留的窗口数 (与脚本硬编码 8 一致)
    refine_keep: int = 6  # 细化阶段处理的候选窗口数 (与脚本硬编码 6 一致)
    warm_x: list | None = field(default_factory=lambda: list(DEFAULT_WARM_X))
    run_scan: bool = True             # [1] 窗口粗扫 (含 [2] 细化, 与脚本耦合)
    run_seed: bool = True             # [3] 弹道播种 (smoke 时脚本自动跳过)
    run_compress: bool = True         # [4] 宽 TOF 压缩 (种子解 ±25% 盒)
    run_frontier: bool = True         # [5] 紧 TOF 压缩 (种子解 ±12% 盒, 强罚)

    # ------------------------------------------------------------------
    def validate(self):
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
                    or not _num(b[0]) or not _num(b[1])):
                raise ValueError(f"tof_bounds[{i}] 应为 [lo, hi] 数值, 实际 {b}")
            if b[0] > b[1]:
                raise ValueError(f"tof_bounds[{i}] lo > hi, 实际 {b}")
        if not (_num(self.rp_ub) and self.rp_ub > 0):
            raise ValueError(f"rp_ub 应为正数, 实际 {self.rp_ub}")
        if (not isinstance(self.vinf_bounds_kmps, (list, tuple))
                or len(self.vinf_bounds_kmps) != 2
                or not all(_num(v) for v in self.vinf_bounds_kmps)):
            raise ValueError(f"vinf_bounds_kmps 应为 [lo, hi] 两个数值, 实际 {self.vinf_bounds_kmps}")
        if self.vinf_bounds_kmps[0] > self.vinf_bounds_kmps[1]:
            raise ValueError(f"vinf_bounds_kmps lo > hi, 实际 {self.vinf_bounds_kmps}")
        if (not isinstance(self.eta_bounds, (list, tuple)) or len(self.eta_bounds) != 2
                or not all(_num(v) for v in self.eta_bounds)
                or not (0 < self.eta_bounds[0] <= self.eta_bounds[1] <= 1)):
            raise ValueError(f"eta_bounds 应为 0 < lo <= hi <= 1, 实际 {self.eta_bounds}")
        for fname in ("penalty", "frontier_penalty"):
            v = getattr(self, fname)
            if (not isinstance(v, (list, tuple)) or len(v) != 2
                    or not all(_num(x) and x >= 0 for x in v)):
                raise ValueError(f"{fname} 应为 [线性权, 二次权] 两个非负数值, 实际 {v}")
        for fname in ("dsm_limit_ms", "wl", "wa",
                      "vinf_launch_limit_ms", "vinf_arrival_limit_ms"):
            v = getattr(self, fname)
            if not (_num(v) and v >= 0):
                raise ValueError(f"{fname} 应为非负数值, 实际 {v}")
        for k, v in self.safe_radius.items():
            if not (_num(v) and v > 0):
                raise ValueError(f"safe_radius[{k}] 应为正数 (m), 实际 {v!r}")
        if not (1 <= self.jobs <= 256):
            raise ValueError(f"jobs 应在 1-256, 实际 {self.jobs}")
        if self.objective not in ("min_tof", "min_dsm", "custom"):
            raise ValueError(f"objective 应为 min_tof/min_dsm/custom, 实际 {self.objective}")
        if self.objective == "custom" and len(self.objective_weights) != 2:
            raise ValueError(f"objective_weights 应为 [TOF权重, DSM权重], 实际 {self.objective_weights}")
        if self.scan_keep < 1 or self.refine_keep < 1:
            raise ValueError("scan_keep / refine_keep >= 1")
        if self.era_step_d is not None and self.era_step_d < 1:
            raise ValueError("era_step_d (搜索步进) >= 1 天")
        # de440s 星历覆盖约 1849-2150 (MJD2000 约 ±54820); 范围外所有窗口都会失败
        EPH_LO, EPH_HI = -54000.0, 54000.0
        _epoch0 = datetime.date(2000, 1, 1)
        for e in self.eras:
            if len(e) != 2 or not e[0] or not e[1]:
                raise ValueError(f"era 应为 [start, end], 实际 {e}")
            for label, s in (("start", e[0]), ("end", e[1])):
                try:
                    d = datetime.date.fromisoformat(s)
                except (TypeError, ValueError):
                    raise ValueError(f"era {label} 日期格式应为 YYYY-MM-DD, 实际 {s!r}")
                mjd = float((d - _epoch0).days)
                if not (EPH_LO <= mjd <= EPH_HI):
                    raise ValueError(
                        f"era {label} 超出 de440s 星历范围 (1849-2150): {s}")
            if e[0] > e[1]:
                raise ValueError(f"era start > end, 实际 {e}")
        # direct 编码: 4 (发射) + 2n (每腿 eta,T) + 2(n-1) (每飞掠 beta,rp) = 4n+2
        if self.warm_x is not None:
            if not all(_num(v) for v in self.warm_x):
                raise ValueError("warm_x 应为数值向量")
            if len(self.warm_x) != 4 * n_legs + 2:
                raise ValueError(
                    f"warm_x 长度应为 {4 * n_legs + 2} ({n_legs} 腿 direct 编码), 实际 {len(self.warm_x)}")
        return True

    # ------------------------------------------------------------------
    def to_dict(self):
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d):
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kw = {k: v for k, v in d.items() if k in known}
        cfg = cls(**kw)
        cfg.validate()
        return cfg

    def to_json(self, path=None, indent=2):
        txt = json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
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

    def era_mjd(self):
        """时代窗口 (MJD2000 对) — 供 stages 使用."""
        import pykep as pk
        return [[pk.epoch(s).mjd2000, pk.epoch(e).mjd2000] for s, e in self.eras]


def _num(v):
    """数值判定 (bool 除外)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf"))


def sanitize_name(name):
    """任务名 -> 安全目录名."""
    s = re.sub(r"[^\w\-]+", "_", name).strip("_") or "job"
    return s[:40]


# ---------------------------------------------------------------------------
# 内置任务预设: 只含任务设置 (计算参数已独立为计算预设)
# ---------------------------------------------------------------------------
def preset_evveju():
    """EVVEJU: Earth→Venus→Venus→Earth→Jupiter→Uranus (默认, 含内置热启动)."""
    return TrajConfig(name="EVVEJU")


def preset_evvejs_cassini():
    """Cassini 号 (1997-10 发射): E→V→V→E→J→Saturn, 实测飞行 ~6.7 yr (无内置热启动)."""
    cfg = TrajConfig(name="EVVEJS Cassini 1997-10")
    cfg.seq = ["EARTH", "VENUS", "VENUS", "EARTH", "JUPITER", "SATURN"]
    cfg.eras = [["1997-01-01", "1997-12-31"]]
    cfg.tof_bounds = [
        [170.0, 220.0],    # Earth -> Venus1  (Cassini 1997-10-15→1998-04-26, ~193 d)
        [400.0, 450.0],    # Venus1 -> Venus2 (Cassini 1998-04-26→1999-06-24, ~424 d)
        [40.0, 90.0],      # Venus2 -> Earth2 (Cassini 1999-06-24→1999-08-18, ~55 d)
        [450.0, 700.0],    # Earth2 -> Jupiter (Cassini 1999-08-18→2000-12-30, ~500 d)
        [1100.0, 1500.0],  # Jupiter -> Saturn (Cassini 2000-12-30→2004-07-01, ~1279 d)
    ]
    cfg.warm_x = None
    return cfg


PRESETS = {
    "EVVEJU (默认, 含热启动)": preset_evveju,
    "EVVEJS 卡西尼号 (1997-10)": preset_evvejs_cassini,
}