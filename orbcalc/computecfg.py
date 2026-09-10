# -*- coding: utf-8 -*-
"""
ComputeConfig — 全局计算配置 (唯一一份, 区别于每任务的 TrajConfig)。

前端"计算配置"表单的持久化: 表单初始值取自这里, 每次提交任务后自动更新
(重启后恢复上次提交时的计算设置)。文件缺失/损坏时回退 TrajConfig 默认值。

字段:
- run_scan / run_seed / run_compress / run_frontier: 流水线阶段开关
- smoke: 冒烟 (快速) 模式
- scan_keep / refine_keep: 扫描/细化保留窗口数
- era_step_d: 搜索步进 (天; null = 自动: smoke?90:60)
- jobs: 并行进程数
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict

from .config import TrajConfig

COMPUTE_FILE = "orbitcalculator.compute.json"


def _base_dir():
    """可执行文件(冻结)/工作目录(开发)所在目录."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.dirname(sys.executable)
    return os.getcwd()


def computecfg_path(preferred=None):
    """返回 ComputeConfig 文件路径: 显式 > 程序目录"""
    if preferred:
        return preferred
    return os.path.join(_base_dir(), COMPUTE_FILE)


def _traj_default(key):
    """从 TrajConfig 默认值取字段默认 (单一事实来源, 避免两处漂移)."""
    def f():
        return TrajConfig().to_dict()[key]
    return f


@dataclass
class ComputeConfig:
    run_scan: bool = field(default_factory=_traj_default("run_scan"))
    run_seed: bool = field(default_factory=_traj_default("run_seed"))
    run_compress: bool = field(default_factory=_traj_default("run_compress"))
    run_frontier: bool = field(default_factory=_traj_default("run_frontier"))
    smoke: bool = field(default_factory=_traj_default("smoke"))
    scan_keep: int = field(default_factory=_traj_default("scan_keep"))
    refine_keep: int = field(default_factory=_traj_default("refine_keep"))
    era_step_d: float | None = field(default_factory=_traj_default("era_step_d"))
    jobs: int = field(default_factory=_traj_default("jobs"))

    # ------------------------------------------------------------------
    def validate(self):
        # 复用 TrajConfig 完整校验 (自动补轨迹默认值, 再校验计算字段)
        TrajConfig.from_dict(self.to_dict())
        return True

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kw = {k: v for k, v in (d or {}).items() if k in known}
        cfg = cls(**kw)
        cfg.validate()
        return cfg

    def to_json(self, path=None, indent=2):
        txt = json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(txt)
        return txt

    @classmethod
    def from_json(cls, path=None, text=None):
        if text is None:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        return cls.from_dict(json.loads(text))

    def __str__(self):
        return f"ComputeConfig(jobs={self.jobs}, smoke={self.smoke})"


# 计算字段名集合 (webapp 视图/裁剪用)
COMP_FIELDS = {f.name for f in ComputeConfig.__dataclass_fields__.values()}


def default_computecfg():
    return ComputeConfig()


def load_computecfg(preferred=None):
    """从磁盘加载; 文件不存在回退默认; JSON 损坏回退默认 (不抛异常)."""
    path = computecfg_path(preferred)
    try:
        return ComputeConfig.from_json(path=path)
    except Exception:
        return ComputeConfig()


def save_computecfg(cfg, preferred=None):
    """保存到磁盘; 返回实际路径."""
    path = computecfg_path(preferred)
    cfg.to_json(path=path)
    return path
