# -*- coding: utf-8 -*-
"""
发射窗口集合 (era)。

era 是一段允许发射的日期区间; 一个任务可含多段 (可不相交)。本模块把
「日期字符串 -> MJD2000 区间」的解析/校验, 以及与 t0 盒求交/窗口生成
收在一处, 供 config / stages 复用 (纯 datetime, 不依赖 pykep)。

术语:
    range : 单段 era 的 MJD2000 区间 [lo, hi]
    box   : 以某 anchor 为中心、半宽 half 的 t0 搜索盒 [anchor-half, anchor+half]
"""
from __future__ import annotations

import datetime

_EPOCH0 = datetime.date(2000, 1, 1)
_EPH_LO, _EPH_HI = -54000.0, 54000.0     # de440s 星历覆盖 (~1849-2150)


class EraSet:
    """多段发射窗口 [lo, hi] (MJD2000) 的集合。"""

    def __init__(self, ranges):
        self.ranges = [[float(lo), float(hi)] for lo, hi in ranges]

    @classmethod
    def from_dates(cls, eras) -> "EraSet":
        """由 [["YYYY-MM-DD", "YYYY-MM-DD"], ...] 解析并校验。"""
        ranges = []
        for e in eras:
            if not isinstance(e, (list, tuple)) or len(e) != 2 or not e[0] or not e[1]:
                raise ValueError(f"era 应为 [start, end], 实际 {e}")
            pair = []
            for label, s in (("start", e[0]), ("end", e[1])):
                try:
                    d = datetime.date.fromisoformat(s)
                except (TypeError, ValueError):
                    raise ValueError(f"era {label} 日期格式应为 YYYY-MM-DD, 实际 {s!r}")
                mjd = float((d - _EPOCH0).days)
                if not (_EPH_LO <= mjd <= _EPH_HI):
                    raise ValueError(f"era {label} 超出 de440s 星历范围 (1849-2150): {s}")
                pair.append(mjd)
            if pair[0] > pair[1]:
                raise ValueError(f"era start > end, 实际: {e}")
            ranges.append(pair)
        return cls(ranges)

    def __len__(self):
        return len(self.ranges)

    def __iter__(self):
        return iter(self.ranges)

    def __repr__(self):
        return f"EraSet({[list(r) for r in self.ranges]!r})"

    def contains(self, t) -> bool:
        """t (MJD2000) 是否落在任一 era 内。"""
        return any(lo <= t <= hi for lo, hi in self.ranges)

    def intersect(self, lo, hi) -> list[list[float]]:
        """[lo, hi] 与各 era 的交集 (数学意义)。

        返回非空子区间列表; 无交集返回 [] (与集合运算一致)。
        """
        out = []
        for r_lo, r_hi in self.ranges:
            a, b = max(lo, r_lo), min(hi, r_hi)
            if a <= b:
                out.append([a, b])
        return out

    def clip(self, anchor, half) -> list[float]:
        """以 anchor 为中心、半宽 half 的 t0 盒与 era 求交。

        优先返回含 anchor 的那段交集, 否则返回首个相交段;
        无交集返回 [] (调用方应跳过该盒)。
        """
        half = float(half)
        segs = self.intersect(anchor - half, anchor + half)
        for seg in segs:
            if seg[0] <= anchor <= seg[1]:
                return seg
        return segs[0] if segs else []

    def windows(self, step) -> list[float]:
        """按 step (天) 在每个 era 内生成窗口中心 (含左端, 不超过右端)。"""
        step = float(step)
        if step <= 0:
            raise ValueError(f"step 应为正数, 实际 {step!r}")
        out = []
        for lo, hi in self.ranges:
            t = lo
            while t <= hi:
                out.append(t)
                t += step
        return out
