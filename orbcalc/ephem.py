# -*- coding: utf-8 -*-
"""
外部星历 (SPICE kernel) 扫描 / 启用 / 加载。

- 目录: 运行根下的 ``ephemeris/`` (缺失时由 web 层创建; 用户放入 ``*.bsp``)。
- 扫描: 对每个 ``.bsp`` 用 ``pk.utils.inspect_spice_kernel`` 枚举 NAIF id 并取名,
  用 ``pk.utils.extract_coverage_window`` 取覆盖期 (文件级取并集)。
- 加载: ``ensure_loaded`` 每进程对每个 kernel ``furnsh`` 一次 (模块级去重)。
- 启用: ``set_active`` 指定启用哪些文件 (进程级状态; 由 settings/job cfg 驱动)。
"""
from __future__ import annotations

import re
from pathlib import Path

DIRNAME = "ephemeris"

_scan_cache: list | None = None
_active: list[str] = []
_loaded: set[str] = set()


def dir_path() -> Path:
    return Path.cwd() / DIRNAME


def tag_from_name(name) -> str | None:
    """天体名 -> 合法 TAG (大写、非字母数字转下划线); 空则 None。"""
    t = re.sub(r"[^A-Za-z0-9]+", "_", str(name)).strip("_").upper()
    return t or None


# ---------------------------------------------------------------------------
def _scan_one(path: Path) -> dict:
    import pykep as pk

    info = {"file": path.name, "size": path.stat().st_size, "bodies": [], "coverage": None}
    try:
        naifs = [int(n) for n in pk.utils.inspect_spice_kernel(str(path))]
    except Exception:
        naifs = []
    cov = None
    for nid in naifs:
        try:
            name = pk.utils.naifid2name(nid)
        except Exception:
            name = "Not Found"
        if not name or name == "Not Found":
            name = f"NAIF {nid}"
        info["bodies"].append({"naif": nid, "name": name})
        try:
            e0, e1 = pk.utils.extract_coverage_window(str(path), nid, 0)
            a, b = float(e0.mjd2000), float(e1.mjd2000)
            cov = (a, b) if cov is None else (min(cov[0], a), max(cov[1], b))
        except Exception:
            pass
    info["coverage"] = cov
    return info


def scan(force: bool = False) -> list:
    """扫描 ``ephemeris/*.bsp`` -> [{file,size,bodies,coverage}]; 结果缓存。"""
    global _scan_cache
    if _scan_cache is not None and not force:
        return _scan_cache
    out = []
    d = dir_path()
    if d.is_dir():
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() == ".bsp":
                out.append(_scan_one(f))
    _scan_cache = out
    return out


def options() -> list:
    """供设置页: [{file,size,n_bodies,coverage}]。"""
    return [{"file": k["file"], "size": k["size"], "n_bodies": len(k["bodies"]),
             "coverage": k["coverage"]} for k in scan()]


def reset_cache() -> None:
    global _scan_cache
    _scan_cache = None


# ---------------------------------------------------------------------------
def set_active(files) -> None:
    """设置启用的 kernel 文件名 (按给定顺序; 未知/不存在的在解析时忽略)。"""
    global _active
    _active = [str(x) for x in (files or [])]


def active_files() -> list[str]:
    return list(_active)


def active_kernels() -> list:
    """已启用且在扫描结果中的 kernel (保持启用顺序)。"""
    byname = {k["file"]: k for k in scan()}
    return [byname[f] for f in _active if f in byname]


def ensure_loaded(files=None) -> None:
    """对给定/已启用的 kernel 逐进程 ``furnsh`` 一次 (去重)。"""
    import pykep as pk

    d = dir_path()
    for f in (files if files is not None else _active):
        p = (d / f).resolve()
        key = str(p)
        if key in _loaded or not p.exists():
            continue
        pk.utils.load_spice_kernels(key)
        _loaded.add(key)


def loaded_paths() -> set[str]:
    return set(_loaded)
