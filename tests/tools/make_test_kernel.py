# -*- coding: utf-8 -*-
"""
生成用于测试的小型小行星 SPK 星历 (SPK Type 9), 输出到 ``ephemeris/``。

- 目的: 为「外部星历」功能提供一两个可用的 ``*.bsp`` (文件很小, ~几十 KB)。
- 数据: 近似二体轨道 (J2000 根数), 不是真实精密星历 —— 仅用于功能测试。
- ``ephemeris/*`` 已被 .gitignore 忽略, 不会入库; 需要时随时可用本脚本重生成:

    python tests/tools/make_test_kernel.py            # 生成 ceres_test.bsp / vesta_test.bsp
    python tests/tools/make_test_kernel.py --list     # 列出将写入的天体
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pykep as pk
import spiceypy as sp

ET0 = 43135.816087188054            # str2et("2000-01-01 00:00:00 UTC")
T0_MJD, T1_MJD, NKNOTS, DEGREE = 0.0, 15000.0, 300, 5   # 2000..2041, ~50 天一个点

# name -> (NAIF id, segment id, [a(AU), e, i, RAAN, argp, M0] 角度为度, @J2000)
BODIES = {
    "ceres_test": (2000001, "CERES TEST",
                   [2.7692, 0.0758, 10.59, 80.33, 73.6, 95.99]),
    "vesta_test": (2000004, "VESTA TEST",
                   [2.3615, 0.0887, 7.14, 103.81, 151.20, 20.0]),
    "pallas_test": (2000002, "PALLAS TEST",
                    [2.7720, 0.2313, 34.84, 173.02, 310.0, 40.0]),
}


def _kepler_state(el, mu):
    a = el[0] * pk.AU
    e = el[1]
    i, W, w, M = (math.radians(x) for x in el[2:6])
    E = M
    for _ in range(80):                       # 平近点角 -> 偏近点角
        E -= (E - e * math.sin(E) - M) / (1.0 - e * math.cos(E))
    nu = 2.0 * math.atan2(math.sqrt(1 + e) * math.sin(E / 2.0),
                          math.sqrt(1 - e) * math.cos(E / 2.0))
    return pk.par2ic([a, e, i, W, w, nu], mu)


def build_states(el, mu):
    r0, v0 = _kepler_state(el, mu)
    epochs = np.linspace(T0_MJD, T1_MJD, NKNOTS)
    states = []
    for m in epochs:
        r, v = pk.propagate_lagrangian([r0, v0], (m - T0_MJD) * pk.DAY2SEC, mu)
        states.append([r[0] / 1000, r[1] / 1000, r[2] / 1000,   # SPK 用 km, km/s
                       v[0] / 1000, v[1] / 1000, v[2] / 1000])
    ets = [-ET0 + m * pk.DAY2SEC for m in epochs]
    return np.array(states), ets


def write_spk(path: Path, naif: int, segid: str, states, ets):
    handle = sp.spkopn(str(path), "PASTA test kernels", 0)
    try:
        sp.spkw09(handle, naif, 0, "ECLIPJ2000", ets[0], ets[-1], segid,
                  DEGREE, len(ets), states, ets)
    finally:
        sp.spkcls(handle)


def main():
    ap = argparse.ArgumentParser(description="生成测试用小行星 SPK")
    ap.add_argument("--outdir", default="ephemeris", help="输出目录 (默认 ephemeris/)")
    ap.add_argument("--list", action="store_true", help="只列出将写入的天体")
    args = ap.parse_args()

    if args.list:
        for name, (naif, segid, _) in BODIES.items():
            print(f"{name}: naif={naif} segid={segid!r}")
        return

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    mu = pk.MU_SUN
    for name, (naif, segid, el) in BODIES.items():
        path = out / f"{name}.bsp"
        flat, ets = build_states(el, mu)
        write_spk(path, naif, segid, flat, ets)
        print(f"wrote {path} ({path.stat().st_size/1024:.1f} KB), "
              f"naif={naif}, {len(ets)} knots, {T0_MJD:.0f}..{T1_MJD:.0f} mjd2000")


if __name__ == "__main__":
    main()
