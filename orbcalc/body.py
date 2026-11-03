# -*- coding: utf-8 -*-
"""天体 (Body) 领域模型: 标识、物理参数与星历来源。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Body:
    tag: str
    name: str
    naif: int
    mu_self: float = 0.0
    radius: float = 0.0
    safe_radius: float = 0.0
    kernel: str | None = None          # None = 内置 de440s
    de440s_name: str | None = None     # 仅内置天体使用的 SPICE 名
    horizons: str | None = None        # 下载来源命令 (校验/重建用)

    @property
    def builtin(self) -> bool:
        return self.kernel is None

    @property
    def spice_target(self) -> str | int:
        """内置天体用 de440s 名; 外部天体用 NAIF id。"""
        return self.de440s_name if self.builtin else self.naif

    @property
    def label(self) -> str:
        s = str(self.name or self.tag)
        return s if s[:1].isdigit() else s.capitalize()

    @property
    def note(self) -> str:
        if self.builtin:
            return ""
        no_phys = self.mu_self <= 0 and self.radius <= 0
        return "μ = 0" if no_phys else "外部星历"

    def info(self) -> dict:
        """``/api/bodies`` 的一项。"""
        return {"label": self.label, "note": self.note,
                "builtin": self.builtin, "kernel": self.kernel}
