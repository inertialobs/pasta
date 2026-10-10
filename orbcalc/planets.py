# -*- coding: utf-8 -*-
"""
天体物理参数表 + 星历封装。

- 物理参数 (μ_self, 半径, safe_radius): 内置 BODY_PARAMS 提供 (行星/矮行星), 未列入
  天体默认 μ=0/半径=0/safe=0。
- 星历 (位置/速度): 内置行星用 de440s; 其它天体 (矮行星/小行星/…) 来自已启用的外部
  SPICE kernel (ephemeris/*.bsp, 见 orbcalc/ephem.py), 用 pk.udpla.spice 读取。
- 中心天体统一为太阳 (pk.MU_SUN)。

历史: 原先 μ/半径 来自 pk.udpla.jpl_lp; 现抄入 BODY_PARAMS 以统一来源, 数值逐位保持。
"""
from __future__ import annotations

import pykep as pk

from . import ephem

# 中心天体 GM (= jpl_lp 的 mu_central_body 值, 与 pk.MU_SUN 逐位一致)
MU_CENTRAL = pk.MU_SUN

# 内置天体物理参数: TAG -> spec
#   naif        : NAIF id (kernel 枚举/去重用)
#   spice       : de440s 中的 NAIF 名; None 表示星历需来自外部 kernel
#   name        : 简短名 (报告/绘图)
#   mu_self     : 自身体 GM [m^3/s^2]
#   radius      : 赤道半径 [m]
#   safe_radius : 飞掠安全半径 [m]
BODY_PARAMS = {
    "MERCURY": {"naif": 199, "spice": "MERCURY", "name": "mercury",
                "mu_self": 22032000000000.0, "radius": 2440000.0, "safe_radius": 2640000.0},
    "VENUS":   {"naif": 299, "spice": "VENUS", "name": "venus",
                "mu_self": 324859000000000.0, "radius": 6052000.0, "safe_radius": 6252000.0},
    "EARTH":   {"naif": 399, "spice": "EARTH", "name": "earth",
                "mu_self": 398600441800000.0, "radius": 6378000.0, "safe_radius": 6578137.0},
    "MARS":    {"naif": 4, "spice": "MARS BARYCENTER", "name": "mars",
                "mu_self": 42828000000000.0, "radius": 3397000.0, "safe_radius": 3589500.0},
    "JUPITER": {"naif": 5, "spice": "JUPITER BARYCENTER", "name": "jupiter",
                "mu_self": 1.26686534e+17, "radius": 71492000.0, "safe_radius": 142984000.0},
    "SATURN":  {"naif": 6, "spice": "SATURN BARYCENTER", "name": "saturn",
                "mu_self": 3.7931187e+16, "radius": 60330000.0, "safe_radius": 116464000.0},
    # URANUS 原 REGISTRY 安全半径为 None (回退 jpl_lp), 此处直接写入该值以保持逐位一致
    "URANUS":  {"naif": 7, "spice": "URANUS BARYCENTER", "name": "uranus",
                "mu_self": 5793939000000000.0, "radius": 25362000.0, "safe_radius": 27898200.000000004},
    "NEPTUNE": {"naif": 8, "spice": "NEPTUNE BARYCENTER", "name": "neptune",
                "mu_self": 6836529000000000.0, "radius": 24622000.0, "safe_radius": 49244000.0},
}

# 未列入表的天体默认物理参数 (小行星/彗星: 质量与半径按 0 处理)
DEFAULT_BODY = {"mu_self": 0.0, "radius": 0.0, "safe_radius": 0.0}

DEFAULT_SAFE_RADIUS = {tag: spec["safe_radius"] for tag, spec in BODY_PARAMS.items()}

# 简名 (报告/绘图用) — 同一 TAG 多次出现时自动加序号 (Venus1, Venus2, ...)
TAG_SHORT = {tag: spec["name"].capitalize() for tag, spec in BODY_PARAMS.items()}


# ---------------------------------------------------------------------------
def set_active(files):
    """设置启用的外部星历 (转发 ephem 并清空行星缓存)。"""
    ephem.set_active(files)
    _cache.clear()


def _kernel_bodies():
    """已启用 kernel 的天体: tag -> {naif, name, file}; 与内置按 NAIF 去重, 先启用者优先。"""
    builtin_by_naif = {s["naif"]: t for t, s in BODY_PARAMS.items()}
    out = {}
    for k in ephem.active_kernels():
        for b in k["bodies"]:
            tag = (builtin_by_naif.get(b["naif"]) or ephem.tag_from_name(b["name"])
                   or f"NAIF_{b['naif']}")
            out.setdefault(tag, {"naif": b["naif"], "name": b["name"], "file": k["file"]})
    return out


def _resolve(tag):
    """tag -> (spec, kernel_file)。kernel_file=None 表示用内置 de440s; 未知 (None, None)。

    spec: {naif, spice(名或NAIF id), name, mu_self, radius, safe_radius}。
    """
    base = BODY_PARAMS.get(tag)
    if base is not None and base.get("spice"):
        return dict(base), None
    kb = _kernel_bodies().get(tag)
    if kb is None:
        return None, None
    if base is not None:                       # 表里有物理参数 (如矮行星), 星历来自 kernel
        spec = dict(base)
    else:                                      # 表外天体 (小行星等): 默认 μ=0/半径=0
        spec = {"name": kb["name"], "mu_self": DEFAULT_BODY["mu_self"],
                "radius": DEFAULT_BODY["radius"], "safe_radius": DEFAULT_BODY["safe_radius"]}
    spec["naif"] = kb["naif"]
    spec["spice"] = kb["naif"]                 # 用 NAIF id 直接定位 (名可能不规范)
    return spec, kb["file"]


def known_tags():
    """可用天体 TAG: 内置行星 ∪ 已启用 kernel 天体。"""
    tags = {t for t, s in BODY_PARAMS.items() if s.get("spice")}
    tags |= set(_kernel_bodies())
    return tags


def body_info(tag):
    """供 /api/bodies: {label, note, builtin, kernel} 或 None。"""
    spec, kfile = _resolve(tag)
    if spec is None:
        return None
    return {"label": spec.get("name", tag).capitalize(),
            "note": "" if kfile is None else "外部星历 (kernel)",
            "builtin": kfile is None,
            "kernel": kfile}


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


def make_planet(spice, params, safe_radius_m=None, kernel=None):
    """按物理参数 spec 构建 pk.planet。

    kernel=None: 位置来自内置 de440s; 否则来自已加载的 SPICE kernel (pk.udpla.spice)。
    """
    if kernel is not None:
        ephem.ensure_loaded([kernel])
        eph = pk.planet(pk.udpla.spice(spice, "ECLIPJ2000", "SSB"))
        source = "kernel"
    else:
        eph = pk.planet(pk.udpla.de440s(spice, "ECLIPJ2000", "SSB"))
        source = "de440s"
    phys = ConstPhys(
        mu_self=params["mu_self"], radius=params["radius"],
        safe_radius=params["safe_radius"], mu_central=MU_CENTRAL,
        name=params.get("name", str(spice)),
    )
    return pk.planet(HybridPlanet(eph, phys, params.get("name", str(spice)),
                                  safe_radius_m, source=source))


_cache = {}


def get_planet(tag):
    """按 TAG 构建 pk.planet (进程内缓存)."""
    if tag not in _cache:
        spec, kfile = _resolve(tag)
        if spec is None:
            raise KeyError(f"未知行星 TAG: {tag}")
        _cache[tag] = make_planet(spec["spice"], spec, kernel=kfile)
    return _cache[tag]


def planet_safe_radius(tag, cfg):
    """行星安全半径: cfg.safe_radius 覆盖优先, 否则注册表/默认."""
    if tag in cfg.safe_radius:
        return float(cfg.safe_radius[tag])
    base = BODY_PARAMS.get(tag)
    if base is not None:
        return base["safe_radius"]
    if tag in _kernel_bodies():
        return DEFAULT_BODY["safe_radius"]
    return None


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
        counts[tag] = counts.get(tag, 0) + 1
        tag_safe = planet_safe_radius(tag, cfg)
        spec, kfile = _resolve(tag)
        if spec is None:
            raise KeyError(f"未知行星 TAG: {tag}")
        seq.append(make_planet(spec["spice"], spec, tag_safe, kernel=kfile))
        short = TAG_SHORT.get(tag, spec.get("name", tag).capitalize())
        names.append(f"{short}{counts[tag]}" if counts[tag] > 1 else short)
    _cache[key] = (seq, names)
    return _cache[key]
