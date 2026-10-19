# -*- coding: utf-8 -*-
"""
只读校验: 把本地 ephemeris/*.bsp 的星历与 JPL Horizons 逐点对比。

- 采样点在"生成节点之间"(用不同步长), 以检验 SPICE 本地插值误差。
- 位置/速度分别报告 (km, km/s); Horizons 与 SPK 都用 TDB/SSB/ECLIPJ2000, 无时系误差。
- 需要网络 (访问 Horizons); 不写任何文件。

用法::

    python tests/tools/verify_kernel.py                      # 校验 ephemeris/ 下全部 kernel
    python tests/tools/verify_kernel.py ryugu.bsp            # 指定文件
    python tests/tools/verify_kernel.py --step 7             # 校验采样步长(天), 默认 7
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from orbcalc import ephem, horizons  # noqa: E402


def _spk_states(path: Path, naif: int, ets):
    import spiceypy as sp

    sp.kclear()
    sp.furnsh(str(path))
    try:
        return np.array([sp.spkez(naif, et, "ECLIPJ2000", "NONE", 0)[0] for et in ets])
    finally:
        sp.unload(str(path))


def verify(path: Path, naif: int, step_d: float, margin_d: float = 30.0):
    info = ephem._scan_one(path)
    cov = info.get("coverage")
    if not cov:
        return f"{path.name}: 无覆盖期, 跳过"
    t0, t1 = cov[0] + margin_d, cov[1] - margin_d
    if t1 <= t0:
        return f"{path.name}: 覆盖期过短, 跳过"
    step_d = max(step_d, (t1 - t0) / 4000.0)          # 限制校验点数 (覆盖期可能很长)
    # 用给定的步长在 Horizons 取真实状态 (与生成步长不同 -> 采样在节点之间)
    cmd = str(naif)
    side = ephem.sidecar_for_naif(naif)
    if side and side.get("horizons"):
        cmd = side["horizons"]
    ref, ets, _ = horizons.fetch(cmd, t0, t1, step_d)
    got = _spk_states(path, naif, ets)
    n = min(len(ref), len(got))
    dr = np.linalg.norm(ref[:n, :3] - got[:n, :3], axis=1)          # km
    dv = np.linalg.norm(ref[:n, 3:] - got[:n, 3:], axis=1) * 1000.0  # m/s
    r_au = np.linalg.norm(ref[:n, :3], axis=1) / horizons.__dict__.get("AU", 1.495978707e8)
    return (f"{path.name}: naif={naif} n={n} step={step_d:g}d | "
            f"max|dr|={dr.max():.3f} km (rel { (dr/np.maximum(r_au*1.496e8,1)).max():.2e}) "
            f"| max|dv|={dv.max():.6f} m/s")


def main():
    ap = argparse.ArgumentParser(description="本地 kernel 对 JPL Horizons 只读校验")
    ap.add_argument("files", nargs="*", help="ephemeris/ 下的文件名 (默认全部)")
    ap.add_argument("--step", type=float, default=7.0, help="校验采样步长(天), 默认 7")
    args = ap.parse_args()
    d = ephem.dir_path()
    names = args.files
    if not names:
        names = [k["file"] for k in ephem.scan(force=True)]
    if not names:
        print("ephemeris/ 下没有 *.bsp")
        return
    for name in names:
        path = d / name
        if not path.is_file():
            print(f"{name}: 不存在")
            continue
        for b in ephem._scan_one(path)["bodies"]:
            try:
                print(verify(path, int(b["naif"]), args.step))
            except Exception as e:
                print(f"{name} naif={b['naif']}: 失败 {type(e).__name__}: {str(e).splitlines()[0][:80]}")


if __name__ == "__main__":
    main()
