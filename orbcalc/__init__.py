# -*- coding: utf-8 -*-
"""
orbcalc — 轨道弹道优化计算库 (配置驱动, 无任何 GUI/Web 依赖)

来源: temp/EVVEJU_TOF_1DSM_mp.py 的物理模型 / 目标函数 / 多进程流水线,
将其硬编码 (序列、边界、权重、窗口) 全部参数化为 TrajConfig,
从而"每个轨道一个脚本"变为"一个配置 JSON 驱动一次计算".

模块:
    config       TrajConfig (dict 子类) + 预设 + JSON 读写
    planets      行星混合模型 (de440s 星历 + JPL 物理参数) + 注册表
    udp          TOF_UDP / DSM_UDP (pykep.trajopt.mga_1dsm 封装)
    engines      优化引擎: sade / nlopt(sbplx,cobyla) / compass / xnes / 并行任务
    stages       六阶段流水线 (多进程): scan / refine / seed / compress / pick
    decode_report 解向量解码 / 文本报告 / 结构化摘要
    plot_data    结果 → Plotly 3D JSON / 静态 PNG (Agg)
    run_cli      唯一计算入口: python -m orbcalc.run_cli --config x.json ...

默认值与字段集 (唯一来源):
    默认值即 schema —— 字段集由默认值字典的键派生, 不手写字段名。
"""
__version__ = "0.1.0"

# 轨迹字段: seq/eras/tof_bounds 的值来自预设 (运行时加载), 故仅登记键名
TRAJ_PRESET_KEYS = ("seq", "eras", "tof_bounds")
TRAJ_DEFAULTS = {
    "name": "Cassini",
    "safe_radius": {},                       # TAG -> 半径 m (覆盖 planets 默认)
    "vinf_bounds_kmps": [3.5, 6.0],
    "eta_bounds": [0.01, 0.9],
    "rp_ub": 30.0,                           # 全局飞掠 rp 上界 (pykep mga_1dsm 仅支持标量)
    "objective": "min_tof",                  # "min_tof" | "min_dsm" | "custom"
    "objective_weights": [1.0, 0.0],         # custom: [TOF, DSM] 权重
    "dsm_limit_ms": 1300.0,                  # m/s (硬核验阈值)
    "penalty": [10.0, 0.2],                  # 默认 DSM 越界罚 (线性, 二次)
    "frontier_penalty": [30.0, 2.0],         # 前沿阶段更强罚
    "wl": 2e-5,                              # 发射 v∞ 超 5.0 km/s 罚 (m/s)
    "vinf_launch_limit_ms": 5000.0,
    "wa": 2e-5,                              # 到达 v∞ 超 9.0 km/s 罚 (m/s)
    "vinf_arrival_limit_ms": 9000.0,
}

# 系统字段 (Flask 服务)
SYS_DEFAULTS = {"host": "127.0.0.1", "port": 8765, "open_browser": True}

# 计算字段 (任务的计算/并行参数; 每任务快照写入 runs/<jid>/config.json)
COMPUTE_DEFAULTS = {
    "run_scan": True,                        # [1] 窗口粗扫 (含 [2] 细化)
    "run_seed": True,                        # [3] 弹道播种
    "run_compress": True,                    # [4] 宽 TOF 压缩
    "run_frontier": True,                    # [5] 紧 TOF 压缩
    "scan_keep": 8,                          # 扫描阶段保留的窗口数
    "refine_keep": 6,                        # 细化阶段处理的候选窗口数
    "era_step_d": None,                      # None -> 搜索阶段默认步进
    "jobs": 4,
}

# 字段集 (由默认值键派生, 不手写)
TRAJ_FIELDS = frozenset(TRAJ_DEFAULTS) | frozenset(TRAJ_PRESET_KEYS)
SYS_FIELDS = frozenset(SYS_DEFAULTS)
COMPUTE_FIELDS = frozenset(COMPUTE_DEFAULTS)
