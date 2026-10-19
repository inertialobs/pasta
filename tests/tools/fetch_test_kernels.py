# -*- coding: utf-8 -*-
"""
从 JPL Horizons 生成测试用小天体 SPK (Type 9) 到 ``ephemeris/``。

- 目标: 天问-2 目标 (469219) Kamo'oalewa、隼鸟2目标 (162173) Ryugu、OSIRIS-REx (101955) Bennu。
- 数据源: JPL Horizons 向量 (中心 SSB, 黄道 J2000, km & km/s), 真实星历。
- 同时写旁挂 ``<name>.json`` (μ/半径/覆盖), 供应用自动填物理参数。
- ``ephemeris/*`` 已被 .gitignore 忽略。

用法::

    python tests/tools/fetch_test_kernels.py                 # 全部
    python tests/tools/fetch_test_kernels.py --only ryugu
    python tests/tools/fetch_test_kernels.py --step 20
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from orbcalc import horizons  # noqa: E402

# name -> (NAIF id, Horizons COMMAND)
TARGETS = {
    "kamooalewa": (2469219, "469219;"),
    "ryugu": (2162173, "162173;"),
    "bennu": (2101955, "101955;"),
}


def _short(label: str) -> str:
    return re.sub(r"^\s*\d+\s+", "", str(label)).strip() or str(label)


def main():
    ap = argparse.ArgumentParser(description="从 JPL Horizons 生成小天体测试 SPK")
    ap.add_argument("--outdir", default="ephemeris")
    ap.add_argument("--only", default=None)
    ap.add_argument("--step", type=float, default=0.0, help="采样步长(天); 0=自适应")
    args = ap.parse_args()

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    t0, t1 = horizons.default_span()
    step = args.step or max(3.0, min(5.0, (t1 - t0) / 6000.0))
    items = TARGETS.items() if not args.only else [(args.only, TARGETS[args.only])]
    for name, (naif, cmd) in items:
        try:
            states, ets, meta = horizons.fetch(cmd, t0, t1, step)
        except Exception as e:
            print(f"skip {name}: {type(e).__name__}: {str(e).splitlines()[-1][:90]}")
            continue
        path = out / f"{name}.bsp"
        horizons.write_spk(path, naif, name.upper(), states, ets)
        radius = meta.get("radius")
        side = {"naif": naif, "name": _short(name).capitalize(),
                "mu_self": meta.get("mu_self"), "radius": radius,
                "safe_radius": (1.05 * radius if radius else 0.0),
                "coverage": [(ets[0] + horizons.ET0) / 86400.0, (ets[-1] + horizons.ET0) / 86400.0],
                "horizons": cmd}
        (out / f"{name}.json").write_text(json.dumps(side, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
        print(f"wrote {path} ({path.stat().st_size/1024:.1f} KB), naif={naif}, "
              f"{len(ets)} pts, R={radius} m, mu={meta.get('mu_self')}")


if __name__ == "__main__":
    main()


