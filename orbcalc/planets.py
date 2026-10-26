# -*- coding: utf-8 -*-
"""
天体物理参数表 + 星历封装。

- 物理参数 (μ_self, 半径, safe_radius): 内置 BUILTIN_BODIES 提供 (行星/矮行星), 未列入
  天体默认 μ=0/半径=0/safe=0。
- 星历 (位置/速度): 内置行星用 de440s; 其它天体 (矮行星/小行星/…) 来自已启用的外部
  SPICE kernel (ephemeris/*.bsp, 见 orbcalc/ephem.py), 用 pk.udpla.spice 读取。
- 中心天体统一为太阳 (pk.MU_SUN)。

历史: 原先 μ/半径 来自 pk.udpla.jpl_lp; 现抄入 BUILTIN_BODIES 以统一来源, 数值逐位保持。
"""
from __future__ import annotations

from dataclasses import replace

import pykep as pk

from . import ephem
from .body import Body

# 中心天体 GM (= jpl_lp 的 mu_central_body 值, 与 pk.MU_SUN 逐位一致)
MU_CENTRAL = pk.MU_SUN

# 内置天体: TAG -> Body
#   de440s_name: de440s 中的 NAIF 名; 外部 kernel 天体为 None
BUILTIN_BODIES = {
    "MERCURY": Body("MERCURY", "mercury", 199,
                    mu_self=22032000000000.0, radius=2440000.0,
                    safe_radius=2640000.0, de440s_name="MERCURY"),
    "VENUS": Body("VENUS", "venus", 299,
                  mu_self=324859000000000.0, radius=6052000.0,
                  safe_radius=6252000.0, de440s_name="VENUS"),
    "EARTH": Body("EARTH", "earth", 399,
                  mu_self=398600441800000.0, radius=6378000.0,
                  safe_radius=6578137.0, de440s_name="EARTH"),
    "MARS": Body("MARS", "mars", 4,
                 mu_self=42828000000000.0, radius=3397000.0,
                 safe_radius=3589500.0, de440s_name="MARS BARYCENTER"),
    "JUPITER": Body("JUPITER", "jupiter", 5,
                    mu_self=1.26686534e+17, radius=71492000.0,
                    safe_radius=142984000.0, de440s_name="JUPITER BARYCENTER"),
    "SATURN": Body("SATURN", "saturn", 6,
                   mu_self=3.7931187e+16, radius=60330000.0,
                   safe_radius=116464000.0, de440s_name="SATURN BARYCENTER"),
    # URANUS 原 REGISTRY 安全半径为 None (回退 jpl_lp), 此处直接写入该值以保持逐位一致
    "URANUS": Body("URANUS", "uranus", 7,
                   mu_self=5793939000000000.0, radius=25362000.0,
                   safe_radius=27898200.000000004, de440s_name="URANUS BARYCENTER"),
    "NEPTUNE": Body("NEPTUNE", "neptune", 8,
                    mu_self=6836529000000000.0, radius=24622000.0,
                    safe_radius=49244000.0, de440s_name="NEPTUNE BARYCENTER"),
}

# 无卫星行星: 系统质心 NAIF == 本体中心 NAIF (位置相同), 视为同一内置天体,
# 使其继承内置 μ/半径 且不在下拉里出现 *_BARYCENTER 冗余项。
# (有卫星的行星质心≠本体, 不在此列, 保持各自独立。)
BARYCENTER_ALIASES = {
    1: "MERCURY",   # 1 = MERCURY BARYCENTER  == 199 = MERCURY
    2: "VENUS",     # 2 = VENUS BARYCENTER    == 299 = VENUS
}

# 简名 (报告/绘图用) — 同一 TAG 多次出现时自动加序号 (Venus1, Venus2, ...)
TAG_SHORT = {tag: body.name.capitalize() for tag, body in BUILTIN_BODIES.items()}


def _display(name, tag="") -> str:
    """显示名: 带编号的名字保持原样 (如 "52 Europa"), 纯小写内置名首字母大写。"""
    s = str(name or tag)
    return s if s[:1].isdigit() else s.capitalize()


# ---------------------------------------------------------------------------
def set_active(files):
    """设置启用的外部星历 (转发 ephem 并清空行星缓存)。"""
    ephem.set_active(files)
    _cache.clear()


def available_bodies() -> dict[str, Body]:
    """内置天体 ∪ 已启用 kernel 天体; 按 NAIF 去重, 先启用者优先。

    - 与内置同 NAIF 的 kernel 天体直接忽略, 沿用内置物理参数。
    - 无卫星行星的质心 NAIF 映射到内置 TAG (BARYCENTER_ALIASES)。
    """
    out = dict(BUILTIN_BODIES)
    seen = {body.naif for body in out.values()}
    for e in ephem.active_kernels():
        for body in e.bodies:
            if body.naif in seen or body.naif in BARYCENTER_ALIASES:
                continue
            tag = body.tag or f"NAIF_{body.naif}"
            out.setdefault(tag, replace(body, tag=tag, kernel=e.file))
            seen.add(body.naif)
    return out


def resolve(tag) -> Body | None:
    """TAG -> Body; 未知返回 None。"""
    return available_bodies().get(tag)


def known_tags():
    """可用天体 TAG: 内置行星 ∪ 已启用 kernel 天体。"""
    return set(available_bodies())


def body_info(tag):
    """供 /api/bodies: {label, note, builtin, kernel} 或 None。"""
    body = resolve(tag)
    return body.info() if body is not None else None


class ConstPhys:
    """常量物理参数 (取代 pk.udpla.jpl_lp, 只提供 μ/半径/safe_radius)。"""

    def __init__(self, mu_self, radius, safe_radius, mu_central=MU_CENTRAL, name=None):
        self._mu_self = float(mu_self)
        self._radius = float(radius)
        self._safe = float(safe_radius)
        self._mu_central = float(mu_central)
        self._name = name

    def get_name(self):
        return self._name

    @property
    def mu_self(self):
        return self._mu_self

    @property
    def mu_central_body(self):
        return self._mu_central

    @property
    def radius(self):
        return self._radius

    @property
    def safe_radius(self):
        return self._safe


class HybridPlanet:
    def __init__(self, eph, phys, name, safe_radius_m=None, source="de440s"):
        self._eph = eph
        self._phys = phys
        self._name = name
        self._safe = safe_radius_m
        self._source = source

    def eph(self, when):
        return self._eph.eph(when)

    def get_name(self):
        return self._phys.get_name() if hasattr(self._phys, "get_name") else self._name

    def get_mu_self(self):
        return self._phys.mu_self

    def get_mu_central_body(self):
        return self._phys.mu_central_body

    def get_radius(self):
        return self._phys.radius

    def get_safe_radius(self):
        return self._safe if self._safe is not None else self._phys.safe_radius

    @property
    def mu_self(self):
        return self._phys.mu_self

    @property
    def mu_central_body(self):
        return self._phys.mu_central_body

    @property
    def radius(self):
        return self._phys.radius

    @property
    def safe_radius(self):
        return self._safe if self._safe is not None else self._phys.safe_radius

    def __str__(self):
        return f"{self._name} ({self._source} eph)"


def make_planet(body: Body, safe_radius_m=None):
    """按 Body 构建 pk.planet。

    ``body.kernel`` 为 None 时用内置 de440s, 否则用已加载的外部 SPICE kernel。
    """
    if body.kernel is not None:
        ephem.ensure_loaded([body.kernel])
        eph = pk.planet(pk.udpla.spice(body.spice_target, "ECLIPJ2000", "SSB"))
        source = "kernel"
    else:
        eph = pk.planet(pk.udpla.de440s(body.spice_target, "ECLIPJ2000", "SSB"))
        source = "de440s"
    phys = ConstPhys(
        mu_self=body.mu_self, radius=body.radius,
        safe_radius=body.safe_radius, mu_central=MU_CENTRAL,
        name=body.name,
    )
    return pk.planet(HybridPlanet(eph, phys, body.name,
                                  safe_radius_m, source=source))


_cache = {}


def get_planet(tag):
    """按 TAG 构建 pk.planet (进程内缓存)."""
    if tag not in _cache:
        body = resolve(tag)
        if body is None:
            raise KeyError(f"未知行星 TAG: {tag}")
        _cache[tag] = make_planet(body)
    return _cache[tag]


def planet_safe_radius(tag, cfg):
    """行星安全半径: cfg.safe_radius 覆盖优先, 否则注册表/默认."""
    if tag in cfg.safe_radius:
        return float(cfg.safe_radius[tag])
    body = resolve(tag)
    return body.safe_radius if body is not None else None


def build_seq(cfg):
    """按 cfg.seq 构建 (SEQ: [pk.planet], NAMES: [str]) — 进程内缓存.

    safe_radius 覆盖在行星构建时即生效 (HybridPlanet._safe)。
    """
    key = (tuple(cfg.seq),
           tuple(sorted((k, str(v)) for k, v in cfg.safe_radius.items())),
           tuple(ephem.active_files()))
    if key in _cache:
        return _cache[key]
    seq, names = [], []
    counts = {}
    for tag in cfg.seq:
        body = resolve(tag)
        if body is None:
            raise KeyError(f"未知行星 TAG: {tag}")
        counts[tag] = counts.get(tag, 0) + 1
        tag_safe = planet_safe_radius(tag, cfg)
        seq.append(make_planet(body, tag_safe))
        short = TAG_SHORT.get(tag, _display(body.name, tag))
        names.append(f"{short}{counts[tag]}" if counts[tag] > 1 else short)
    _cache[key] = (seq, names)
    return _cache[key]
