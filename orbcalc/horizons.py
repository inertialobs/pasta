# -*- coding: utf-8 -*-
"""
JPL Horizons / SBDB 取数: 搜索天体 -> 抓取日心状态 -> 写 SPK。

- 检索: SBDB (`sbdb.api?sstr=`) 解析小天体 (须有编号 -> NAIF 2000000+N);
  否则按 NAIF 名/id (行星/卫星/探测器, 用 pk.utils.name2naifid 或整数)。
- 星历: Horizons VECTORS (中心 SSB、ECLIPJ2000、km & km/s), 长跨度自动分段合并。
- 写文件: SPK Type 9 (degree 5), 采样点=Horizons 输出 (TDB), 中间值由 SPICE 本地插值。
- 物理参数: 从 Horizons 响应头解析 GM(km^3/s^2)->mu、RAD(km)->radius。
"""
from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

J2000_JD = 2451545.0
ET0 = 43135.816087188054                  # et = mjd2000*86400 - ET0 (pykep mjd2000 定义)
SBDB_API = "https://ssd-api.jpl.nasa.gov/sbdb.api"
HORIZONS_API = "https://ssd.jpl.nasa.gov/api/horizons.api"
MAX_RECORDS = 4000                       # 单次 Horizons 请求的最大采样点数
DEGREE = 9                               # SPK Type 9 Lagrange 阶数 (高阶层数对近日点高速段很关键)

_NUM = re.compile(r"[-+]?\d+\.\d+(?:[Ee][-+]?\d+)?")
_SAFE_CMD = re.compile(r"^[0-9A-Za-z ;._\-]+$")


def naif_id(number: int) -> int:
    """编号小天体 -> NAIF id (2000000 + 编号)。"""
    return 2000000 + int(number)


def slugify(text: str) -> str:
    """文件名用安全 slug (小写、非字母数字转下划线)。"""
    s = re.sub(r"[^A-Za-z0-9]+", "_", str(text)).strip("_").lower()
    return s or "body"


def _floats(line: str) -> list[float]:
    return [float(x) for x in _NUM.findall(line)]


def _get(url: str, timeout: int = 120) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# 搜索 / 解析
# ---------------------------------------------------------------------------
def _naif_from_name(name: str) -> int:
    import pykep
    return int(pykep.utils.name2naifid(name))


def _sbdb(query: str) -> dict | None:
    try:
        txt = _get(f"{SBDB_API}?{urllib.parse.urlencode({'sstr': query})}", timeout=60)
        data = json.loads(txt)
    except Exception:
        return None
    return data.get("object") if isinstance(data, dict) else None


def search(query: str) -> list:
    """检索候选: 返回 [{kind, naif, label, fullname, orbit_class, horizons}]。"""
    q = str(query or "").strip()
    out = []
    if not q:
        return out
    obj = _sbdb(q)
    if obj:
        des = str(obj.get("des") or "").strip()
        label = obj.get("shortname") or obj.get("fullname") or q
        if des.isdigit():
            out.append({"kind": "small_body", "naif": naif_id(int(des)), "label": label,
                        "fullname": obj.get("fullname"),
                        "orbit_class": (obj.get("orbit_class") or {}).get("name"),
                        "horizons": f"{des};"})
        else:                              # 未编号小天体: 无标准 NAIF id, 暂不支持
            out.append({"kind": "unnumbered", "naif": None, "label": label,
                        "fullname": obj.get("fullname"),
                        "orbit_class": (obj.get("orbit_class") or {}).get("name"),
                        "horizons": None})
    # NAIF 名 / id (行星、卫星、探测器)
    try:
        nid = int(q) if re.fullmatch(r"-?\d+", q) else _naif_from_name(q)
        if all(c.get("naif") != nid for c in out):
            out.append({"kind": "naif", "naif": nid, "label": q,
                        "fullname": q, "orbit_class": None, "horizons": str(nid)})
    except Exception:
        pass
    return out


def resolve(query: str) -> dict | None:
    """query -> 首个可用候选 (优先小天体)。"""
    for c in search(query):
        if c.get("naif") is not None and c.get("horizons"):
            return c
    return None


# ---------------------------------------------------------------------------
# Horizons 取数
# ---------------------------------------------------------------------------
def _cal(mjd2000: float) -> str:
    import pykep as pk
    return pk.epoch(float(mjd2000)).to_datetime().strftime("%Y-%m-%d %H:%M:%S")


def _parse_vectors(text: str):
    lines = text.splitlines()
    try:
        soe = next(i for i, l in enumerate(lines) if "$$SOE" in l)
        eoe = next(i for i, l in enumerate(lines) if "$$EOE" in l)
    except StopIteration:
        raise RuntimeError("Horizons 未返回向量 (检查 COMMAND/时间):\n" + "\n".join(lines[:20]))
    states, ets = [], []
    i = soe + 1
    while i + 2 < eoe:
        head = lines[i].strip()
        if "TDB" not in head:
            i += 1
            continue
        jd = float(head.split("=")[0].split()[0])
        p, v = _floats(lines[i + 1]), _floats(lines[i + 2])
        if len(p) == 3 and len(v) == 3:
            states.append(p + v)
            ets.append((jd - J2000_JD) * 86400.0)
        i += 3
    return np.array(states), ets


def _get_vectors(cmd: str, a: float, b: float, step_d: float):
    sd = max(1, int(round(step_d)))              # Horizons 要求整数天步长
    q = urllib.parse.urlencode({
        "format": "text", "COMMAND": f"'{cmd}'", "EPHEM_TYPE": "VECTORS",
        "CENTER": "'500@0'", "START_TIME": f"'{_cal(a)}'", "STOP_TIME": f"'{_cal(b)}'",
        "STEP_SIZE": f"'{sd} d'", "VEC_TABLE": "2", "REF_PLANE": "ECLIPTIC",
        "OUT_UNITS": "KM-S", "OBJ_DATA": "NO", "MAKE_EPHEM": "YES",
    })
    return _get(f"{HORIZONS_API}?{q}")


def fetch(cmd: str, t0_mjd: float, t1_mjd: float, step_d: float):
    """-> (states[N,6] km & km/s, ets[N], meta)。长跨度自动分段合并, 去重边界;
    meta 来自第一段响应头 (name/mu_self/radius)。"""
    if not _SAFE_CMD.match(str(cmd)):
        raise ValueError(f"非法 Horizons COMMAND: {cmd!r}")
    step_d = max(float(step_d), 0.1)
    span = float(t1_mjd) - float(t0_mjd)
    if span <= 0:
        raise ValueError("时间跨度必须为正")
    n = int(span / step_d) + 1
    nch = 1 if n <= MAX_RECORDS else math.ceil(n / MAX_RECORDS)
    edges = np.linspace(t0_mjd, t1_mjd, nch + 1)
    states, ets, meta = [], [], None
    for i in range(nch):
        text = _get_vectors(cmd, float(edges[i]), float(edges[i + 1]), step_d)
        if meta is None:
            meta = parse_header(text)
        s, e = _parse_vectors(text)
        if ets and e and abs(e[0] - ets[-1]) < 1e-6:
            s, e = s[1:], e[1:]
        states += list(s)
        ets += e
    return np.array(states), ets, meta


def fetch_vectors(cmd: str, t0_mjd: float, t1_mjd: float, step_d: float):
    s, e, _ = fetch(cmd, t0_mjd, t1_mjd, step_d)
    return s, e


def parse_header(text: str) -> dict:
    """从 Horizons 文本头解析名称与物理参数 (GM[km^3/s^2]->mu, RAD[km]->radius)。"""
    out = {"name": None, "mu_self": None, "radius": None}
    m = re.search(r"Target body name:\s*(.+?)\s*\{", text)
    if m:
        out["name"] = m.group(1).strip()
    m = re.search(r"\bGM=\s*([-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?)", text)
    if m:
        out["mu_self"] = float(m.group(1)) * 1e9          # km^3/s^2 -> m^3/s^2
    m = re.search(r"\bRAD=\s*([-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?)", text)
    if m:
        out["radius"] = float(m.group(1)) * 1e3           # km -> m
    return out


def write_spk(path: Path, naif: int, segid: str, states, ets) -> None:
    import spiceypy as sp
    path = Path(path)
    path.unlink(missing_ok=True)              # SPICE 不覆盖已有文件
    handle = sp.spkopn(str(path), "PASTA external ephemeris", 0)
    try:
        sp.spkw09(handle, int(naif), 0, "ECLIPJ2000", ets[0], ets[-1], segid,
                  DEGREE, len(ets), states, ets)
    finally:
        sp.spkcls(handle)


def default_span():
    """默认跨度 = 内置 de440s 的覆盖期 (mjd2000)。"""
    try:
        import pykep as pk
        e0, e1 = pk.utils.extract_coverage_window(pk.udpla.de440s.kernel_file(), 399, 0)
        return float(e0.mjd2000), float(e1.mjd2000)
    except Exception:
        from . import MJD2000_MIN, MJD2000_MAX
        return float(MJD2000_MIN), float(MJD2000_MAX)
