# -*- coding: utf-8 -*-
"""
外部星历 (SPICE kernel) 扫描 / 启用 / 加载。

- 目录: 运行根下的 ``ephemeris/`` (缺失时由 web 层创建; 用户放入 ``*.bsp``)。
- 扫描: 对每个 ``.bsp`` 用 ``pk.utils.inspect_spice_kernel`` 枚举 NAIF id 并取名,
  用 ``pk.utils.extract_coverage_window`` 取覆盖期 (文件级取并集)。
- 旁挂: 同名 ``<stem>.json`` 可提供名字/μ/半径, 兼容单体与多体表 (见 ``_sidecar_bodies``)。
- 异常: NAIF id 超出标准范围时把该 kernel 标为可疑 (``warning``), 不生成垃圾天体。
- 加载: ``ensure_loaded`` 每进程对每个 kernel ``furnsh`` 一次 (模块级去重)。
- 启用: ``set_active`` 指定启用哪些文件 (进程级状态; 由 settings/job cfg 驱动)。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DIRNAME = "ephemeris"

# 名称补充: pykep 内置 NAIF 字典未收录、但常见的小天体 (NAIF id -> 名)
EXTRA_NAMES = {
    2469219: "KAMOOALEWA",     # (469219) Kamo'oalewa, 天问-2 目标
    2486958: "ARROKOTH",       # 已在 pykep 表, 便于说明可省略
}

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
def _load_sidecar(path: Path):
    """读取同名旁挂 ``<stem>.json``。

    兼容两种结构:
    - 旧单体: ``{naif, name, mu_self, radius, safe_radius, coverage, horizons}``
    - 多体表: ``{"<naif>": {同上}, ...}`` 或 ``{"bodies": {"<naif>": {同上}, ...}}``
    """
    try:
        j = path.with_suffix(".json")
        if j.is_file():
            return json.loads(j.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _sidecar_bodies(side) -> dict[int, dict]:
    """旁挂 -> ``{naif: 元数据}``; 旧单体与多体表都归一化为同一结构。"""
    if not isinstance(side, dict):
        return {}
    out: dict[int, dict] = {}
    if side.get("naif") is not None:                 # 旧单体旁挂
        try:
            nid = int(side["naif"])
        except (TypeError, ValueError):
            nid = None
        if nid is not None:
            out[nid] = side
    raw = side.get("bodies")
    if not isinstance(raw, dict):
        raw = side
    for key, val in raw.items():
        if not isinstance(val, dict):
            continue
        try:
            nid = int(val.get("naif", key))
        except (TypeError, ValueError):
            continue
        out[nid] = {**val, "naif": nid}
    return out


def _valid_naif(nid: int) -> bool:
    """NAIF id 合法性启发式: 行星/卫星/小体/航天器之外的 id 通常意味着 kernel 损坏或非标准。"""
    return (nid == 0 or 1 <= nid <= 999
            or 1_000_000 <= nid <= 3_999_999
            or -99_999 <= nid <= -1)


def _scan_one(path: Path) -> dict:
    import pykep as pk

    side = _load_sidecar(path)
    metas = _sidecar_bodies(side)
    info = {"file": path.name, "size": path.stat().st_size, "bodies": [],
            "coverage": None, "sidecar": side, "warning": None}
    try:
        naifs = [int(n) for n in pk.utils.inspect_spice_kernel(str(path))]
    except Exception:
        naifs = []
    bad = [n for n in naifs if not _valid_naif(n)]
    bad_set = set(bad)
    if bad:
        info["warning"] = f"疑似损坏或不兼容: {len(bad)} 个非法 NAIF id（如 {bad[0]}）"
    cov = None
    for nid in naifs:
        if nid in bad_set:                         # 不把垃圾 id 当星体
            continue
        meta = metas.get(nid) or {}
        name = meta.get("name")
        if not name:
            try:
                name = pk.utils.naifid2name(nid)
            except Exception:
                name = None
        if not name or name == "Not Found":        # pykep 字典未收录 -> 我们补充
            name = EXTRA_NAMES.get(nid)
        if not name:
            name = f"NAIF {nid}"
        body = {"naif": nid, "name": name}
        for key in ("mu_self", "radius", "safe_radius"):
            if meta.get(key) is not None:
                try:
                    body[key] = float(meta[key])
                except (TypeError, ValueError):
                    pass
        info["bodies"].append(body)
        try:
            e0, e1 = pk.utils.extract_coverage_window(str(path), nid, 0)
            a, b = float(e0.mjd2000), float(e1.mjd2000)
            cov = (a, b) if cov is None else (min(cov[0], a), max(cov[1], b))
        except Exception:
            c = meta.get("coverage")
            if isinstance(c, (list, tuple)) and len(c) == 2:
                a, b = float(c[0]), float(c[1])
                cov = (a, b) if cov is None else (min(cov[0], a), max(cov[1], b))
    if side and isinstance(side.get("coverage"), (list, tuple)) and len(side["coverage"]) == 2:
        cov = (float(side["coverage"][0]), float(side["coverage"][1]))
    info["coverage"] = cov
    return info


def sidecar_for_naif(naif) -> dict | None:
    """该 NAIF id 的旁挂参数 (若有)。"""
    for k in scan():
        side = k.get("sidecar")
        if side and side.get("naif") is not None and int(side["naif"]) == int(naif):
            return side
    return None


def file_for_naif(naif) -> dict | None:
    """包含该 NAIF id 的 kernel 描述 (若有)。"""
    for k in scan():
        if any(int(b["naif"]) == int(naif) for b in k["bodies"]):
            return k
    return None


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
    """供设置页: [{file,size,n_bodies,coverage,warning}]。"""
    return [{"file": k["file"], "size": k["size"],
             "n_bodies": 0 if k.get("warning") else len(k["bodies"]),
             "coverage": k["coverage"], "warning": k.get("warning")} for k in scan()]


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
    """已启用且在扫描结果中的 kernel (保持启用顺序); 跳过被判为损坏的 kernel。"""
    byname = {k["file"]: k for k in scan()}
    return [byname[f] for f in _active
            if f in byname and not byname[f].get("warning")]


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


def unload(files) -> None:
    """从 SPICE 卸载指定 kernel (删除文件前释放 Windows 文件锁)。"""
    import pykep as pk

    d = dir_path()
    for f in (files or []):
        p = str((d / f).resolve())
        if p in _loaded:
            try:
                pk.utils.unload_spice_kernels(p)
            except Exception:
                pass
            _loaded.discard(p)
