# -*- coding: utf-8 -*-
"""
MGA-1DSM 子类: 每腿可选 Lambert 解 (单圈 / 一圈低能 / 一圈高能)。

pykep 3.0.1 的 mga_1dsm._compute_dvs 写死 cw=False, multi_revs=0 并取解 0,
因此只能得到单圈转移 (类型 1/2)。本模块按 cfg.lambert_types 逐腿选择
lambert_problem 返回的解索引:

    index 0 -> 0 圈 (类型 1/2)
    index 1 -> 1 圈, 第一分支 (类型 3, 低能)
    index 2 -> 1 圈, 第二分支 (类型 4, 高能)
    一般地 圈数 = (index + 1) // 2

pykep 返回的解列表为 [0 圈, 1 圈a, 1 圈b, 2 圈a, 2 圈b, ...], 且解数取决于 TOF:
TOF 不足以容纳所需圈数时解数不够, 此时抛 ValueError, 由 UDP 的 fitness 捕获并
判为不可行 (巨大罚), 解码时则明确报错。

实现说明
--------
- 本类是唯一的 MGA-1DSM 实现 (全单圈与多圈共用)。全 0 时与 pykep 3.0.1 逐位一致,
  因此单圈结果不因统一而变化; tests/test_lambert_types.py 以随机向量逐位校验。
- pykep 升级后的漂移由同一套逐位测试捕获 (不再依赖委托路径)。
- pretty()/plot() 在 pykep 中写死解 #0: 多圈配置下直接拒绝, 不静默输出单圈结果。
- 构造时检查 pykep 依赖的成员; 缺失则快速失败, 版本未验证则告警一次。
"""
from __future__ import annotations

from math import sqrt

import pykep as pk

from . import LAMBERT_INDICES, slog

# 已验证 _compute_dvs 在全单圈输入上与 pykep 逐位一致的版本 (见 tests/test_lambert_types.py)
PYKEP_VERIFIED_VERSIONS = ("3.0.1",)

# MGA1DSM 依赖的 pykep 成员 (构造后检查; 上游改名时在构造处快速失败)
PYKEP_REQUIRED_ATTRS = (
    "n_legs", "common_mu", "_seq", "_add_vinf_arr", "_add_vinf_dep",
    "_orbit_insertion", "_rp_target", "_e_target", "_decode_times_and_vinf",
)

_version_warned = False


def pykep_version_note(version):
    """版本不在已验证集合内时返回告警文本, 否则 None (纯函数, 便于测试)。"""
    if version in PYKEP_VERIFIED_VERSIONS:
        return None
    return (f"pykep {version} 未经 MGA1DSM 逐位验证 "
            f"(已验证: {', '.join(PYKEP_VERIFIED_VERSIONS)}); "
            "请运行 tests/test_lambert_types.py 确认单圈结果未变")


def lambert_revs(index: int) -> int:
    """Lambert 解索引 -> 所需圈数: 0 -> 0, 1/2 -> 1, 3/4 -> 2 ..."""
    return (int(index) + 1) // 2


def _norm(x):
    # 与 pykep.trajopt._mga_1dsm.norm 同一公式 (保证数值逐位一致)
    return sqrt(sum([it * it for it in x]))


class MGA1DSM(pk.trajopt.mga_1dsm):
    """pykep.trajopt.mga_1dsm + 每腿 Lambert 解选择 (lambert_types)。"""

    def __init__(self, *args, lambert_types=None, **kwargs):
        super().__init__(*args, **kwargs)
        missing = [a for a in PYKEP_REQUIRED_ATTRS if not hasattr(self, a)]
        if missing:
            raise RuntimeError(
                f"pykep {pk.__version__} 的 mga_1dsm 缺少 MGA1DSM 依赖的成员 {missing}; "
                "需更新 orbcalc/mga.py")
        global _version_warned
        if not _version_warned:
            _version_warned = True
            note = pykep_version_note(pk.__version__)
            if note:
                slog.wrn("[mga] " + note)

        n = self.n_legs
        types = [0] * n if lambert_types is None else [int(k) for k in lambert_types]
        if len(types) != n:
            raise ValueError(f"lambert_types 应有 {n} 个元素, 实际 {len(types)}")
        for k in types:
            if k not in LAMBERT_INDICES:
                raise ValueError(f"lambert_types 取值应为 {list(LAMBERT_INDICES)} 之一, 实际 {k}")
        self._lambert_types = types
        # 所有腿共用一次 lambert_problem 的圈数上限; 全 0 时为 0 (即 pykep 原调用)
        self._multi_revs = max((lambert_revs(k) for k in types), default=0)

    @property
    def lambert_types(self):
        return list(self._lambert_types)

    # ------------------------------------------------------------------
    def _solve_leg(self, leg, r0, r1, dt):
        """第 leg 腿的 Lambert 解 (v0, v1, 原始解对象); 所选解不存在时抛 ValueError。"""
        lp = pk.lambert_problem(r0, r1, dt, self.common_mu, cw=False,
                                multi_revs=self._multi_revs)
        k = self._lambert_types[leg]
        if k >= len(lp.v0):
            raise ValueError(
                f"leg {leg}: 需要 Lambert 解 #{k} (类型 {k}), 但 TOF 下仅 {len(lp.v0)} 个解 (TOF 过短)")
        return lp.v0[k], lp.v1[k], lp

    def _compute_dvs(self, x):
        """与 pykep 3.0.1 _compute_dvs 同一流程, Lambert 解按 lambert_types 选择。
        全单圈时与 pykep 逐位一致 (tests/test_lambert_types.py 校验)。"""
        # 1 - 解码 TOF 与 v∞ (同 pykep)
        T, Vinfx, Vinfy, Vinfz = self._decode_times_and_vinf(x)

        # 2 - 各交会点历元与星历
        t_P = list([None] * (self.n_legs + 1))
        r_P = list([None] * (self.n_legs + 1))
        v_P = list([None] * (self.n_legs + 1))
        DV = list([0.0] * (self.n_legs + 1))
        for i in range(len(self._seq)):
            t_P[i] = pk.epoch(x[0] + sum(T[0:i]))
            r_P[i], v_P[i] = self._seq[i].eph(t_P[i])
        ballistic_legs = []
        ballistic_ep = []
        lamberts = []

        # 3 - 第 0 腿: 出发 -> DSM (x[4]) -> Lambert -> 到达 seq[1]
        v0 = [a + b for a, b in zip(v_P[0], [Vinfx, Vinfy, Vinfz])]
        ballistic_legs.append((r_P[0], v0))
        ballistic_ep.append(t_P[0].mjd2000)
        r, v = pk.propagate_lagrangian([r_P[0], v0], x[4] * T[0] * pk.DAY2SEC, self.common_mu)

        dt = (1 - x[4]) * T[0] * pk.DAY2SEC
        v_beg_l, v_end_l, lp = self._solve_leg(0, r, r_P[1], dt)
        lamberts.append(lp)

        ballistic_legs.append((r, v_beg_l))
        ballistic_ep.append(t_P[0].mjd2000 + x[4] * T[0])
        DV[0] = _norm([a - b for a, b in zip(v_beg_l, v)])

        # 4 - 其余各腿: 借力 -> 传播至 DSM -> Lambert -> 下一交会点
        for i in range(1, self.n_legs):
            v_out = pk.fb_vout(
                v_in=v_end_l,
                v_pla=v_P[i],
                rp=x[7 + (i - 1) * 4] * self._seq[i].radius,
                beta=x[6 + (i - 1) * 4],
                mu=self._seq[i].mu_self,
            )
            ballistic_legs.append((r_P[i], v_out))
            ballistic_ep.append(t_P[i].mjd2000)
            r, v = pk.propagate_lagrangian(
                [r_P[i], v_out], x[8 + (i - 1) * 4] * T[i] * pk.DAY2SEC, self.common_mu)

            dt = (1 - x[8 + (i - 1) * 4]) * T[i] * pk.DAY2SEC
            v_beg_l, v_end_l, lp = self._solve_leg(i, r, r_P[i + 1], dt)
            lamberts.append(lp)
            DV[i] = _norm([a - b for a, b in zip(v_beg_l, v)])

            ballistic_legs.append((r, v_beg_l))
            ballistic_ep.append(t_P[i].mjd2000 + x[8 + (i - 1) * 4] * T[i])

        # 5 - 到达 v∞ 与可选的出发 v∞ (同 pykep)
        if self._add_vinf_arr:
            DV[-1] = _norm([a - b for a, b in zip(v_end_l, v_P[-1])])
            if self._orbit_insertion:
                DVper = sqrt(DV[-1] * DV[-1] + 2 * self._seq[-1].mu_self / self._rp_target)
                DVper2 = sqrt(
                    2 * self._seq[-1].mu_self / self._rp_target
                    - self._seq[-1].mu_self / self._rp_target * (1.0 - self._e_target)
                )
                DV[-1] = abs(DVper - DVper2)

        if self._add_vinf_dep:
            DV[0] += x[3]

        return (DV, lamberts, T, ballistic_legs, ballistic_ep)

    # ------------------------------------------------------------------
    # pykep 的 pretty()/plot() 写死解 #0, 多圈配置下会静默给出单圈结果: 直接拒绝
    def _refuse_if_multirev(self, what):
        if self._multi_revs > 0:
            raise NotImplementedError(
                f"{what}() 在 pykep 中写死 Lambert 解 #0, 当前 lambert_types={self._lambert_types} "
                "含多圈腿, 不可用; 请使用 orbcalc.decode_report.decode 与 plot_data")

    def pretty(self, x):
        self._refuse_if_multirev("pretty")
        return super().pretty(x)

    def plot(self, x, *args, **kwargs):
        self._refuse_if_multirev("plot")
        return super().plot(x, *args, **kwargs)
