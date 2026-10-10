# -*- coding: utf-8 -*-
"""
从 JPL Horizons 取真实日心状态, 生成测试用小天体 SPK (Type 9) 到 ``ephemeris/``。

- 目标天体: 天问-2 目标 (469219) Kamo'oalewa、隼鸟2目标 (162173) Ryugu、OSIRIS-REx 目标 (101955) Bennu。
- 数据源: JPL Horizons 向量 (中心=SSB, 黄道 J2000 坐标, km & km/s), 真实星历 (非近似)。
- 输出为自洽 SPK (center=0/SSB), 因此无需额外 kernel 即可被 ``pk.udpla.spice`` 直接查询。
- ``ephemeris/*`` 已被 .gitignore 忽略, 不会入库。

用法::

    python tools/fetch_test_kernels.py                 # 全部
    python tools/fetch_test_kernels.py --only kamooalewa
    python tools/fetch_test_kernels.py --t0 2010-01-01 --t1 2050-01-01 --step "20 d"
"""
from __future__ import annotations

import argparse
import re
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import spiceypy as sp

J2000_JD = 2451545.0
API = "https://ssd.jpl.nasa.gov/api/horizons.api"

# name -> (NAIF id, Horizons 命令)
TARGETS = {
    "kamooalewa": (2469219, "469219;"),     # 天问-2
    "ryugu": (2162173, "162173;"),          # 隼鸟2
    "bennu": (2101955, "101955;"),          # OSIRIS-REx
}

_NUM = re.compile(r"[-+]?\d+\.\d+(?:[Ee][-+]?\d+)?")


def _floats(line: str) -> list[float]:
    return [float(x) for x in _NUM.findall(line)]


def fetch_vectors(horizons_id: str, t0: str, t1: str, step: str):
    q = urllib.parse.urlencode({
        "format": "text", "COMMAND": f"'{horizons_id}'", "EPHEM_TYPE": "VECTORS",
        "CENTER": "'500@0'", "START_TIME": f"'{t0}'", "STOP_TIME": f"'{t1}'",
        "STEP_SIZE": f"'{step}'", "VEC_TABLE": "2", "REF_PLANE": "ECLIPTIC",
        "OUT_UNITS": "KM-S", "OBJ_DATA": "NO", "MAKE_EPHEM": "YES",
    })
    with urllib.request.urlopen(f"{API}?{q}", timeout=120) as r:
        text = r.read().decode("utf-8", "replace")
    lines = text.splitlines()
    try:
        soe = next(i for i, l in enumerate(lines) if "$$SOE" in l)
        eoe = next(i for i, l in enumerate(lines) if "$$EOE" in l)
    except StopIteration:
        raise RuntimeError("Horizons 未返回向量 (检查 COMMAND/时间):\n" + "\n".join(lines[:25]))
    states, ets = [], []
    i = soe + 1
    while i + 2 < eoe:
        jd_line = lines[i].strip()
        if "TDB" not in jd_line:
            i += 1
            continue
        jd = float(jd_line.split("=")[0].split()[0])
        p = _floats(lines[i + 1])
        v = _floats(lines[i + 2])
        if len(p) == 3 and len(v) == 3:
            states.append(p + v)                       # km, km/s (ECLIPJ2000 wrt SSB)
            ets.append((jd - J2000_JD) * 86400.0)      # TDB 秒 (ET)
        i += 3
    if len(ets) < 8:
        raise RuntimeError(f"得到的状态点太少: {len(ets)}")
    return np.array(states), ets


def write_spk(path: Path, naif: int, segid: str, states, ets):
    handle = sp.spkopn(str(path), "PASTA fetched test kernels", 0)
    try:
        sp.spkw09(handle, naif, 0, "ECLIPJ2000", ets[0], ets[-1], segid,
                  5, len(ets), states, ets)
    finally:
        sp.spkcls(handle)


def main():
    ap = argparse.ArgumentParser(description="从 JPL Horizons 生成小天体测试 SPK")
    ap.add_argument("--outdir", default="ephemeris")
    ap.add_argument("--only", default=None, help="只取某个目标 (kamooalewa/ryugu/bennu)")
    ap.add_argument("--t0", default="2005-01-01")
    ap.add_argument("--t1", default="2050-01-01")
    ap.add_argument("--step", default="20 d")
    args = ap.parse_args()

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    items = TARGETS.items() if not args.only else [(args.only, TARGETS[args.only])]
    for name, (naif, hid) in items:
        states, ets = fetch_vectors(hid, args.t0, args.t1, args.step)
        path = out / f"{name}.bsp"
        write_spk(path, naif, name.upper(), states, ets)
        print(f"wrote {path} ({path.stat().st_size/1024:.1f} KB), naif={naif}, "
              f"{len(ets)} states, jd {ets[0]/86400+J2000_JD:.1f}..{ets[-1]/86400+J2000_JD:.1f}")


if __name__ == "__main__":
    main()
