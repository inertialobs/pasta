# -*- coding: utf-8 -*-
"""跨模块共享的小工具。"""
from __future__ import annotations

import math


def is_num(v) -> bool:
    """数值判定 (bool 除外, 排除 NaN/Inf; 超大 int 视为有限)."""
    if isinstance(v, bool):
        return False
    return isinstance(v, int) or (isinstance(v, float) and math.isfinite(v))
