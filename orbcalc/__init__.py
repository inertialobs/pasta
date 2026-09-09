# -*- coding: utf-8 -*-
"""
orbcalc — 轨道弹道优化计算库 (配置驱动, 无任何 GUI/Web 依赖)

来源: temp/EVVEJU_TOF_1DSM_mp.py 的物理模型 / 目标函数 / 多进程流水线,
将其硬编码 (序列、边界、权重、窗口) 全部参数化为 TrajConfig,
从而"每个轨道一个脚本"变为"一个配置 JSON 驱动一次计算".

模块:
    config       TrajConfig dataclass + 预设 + JSON 读写
    planets      行星混合模型 (de440s 星历 + JPL 物理参数) + 注册表
    udp          TOF_UDP / DSM_UDP (pykep.trajopt.mga_1dsm 封装)
    engines      优化引擎: sade / nlopt(sbplx,cobyla) / compass / xnes / 并行任务
    stages       六阶段流水线 (多进程): scan / refine / seed / compress / pick
    decode_report 解向量解码 / 文本报告 / 结构化摘要
    plot_data    结果 → Plotly 3D JSON / 静态 PNG (Agg)
    run_cli      唯一计算入口: python -m orbcalc.run_cli --config x.json ...
"""
__version__ = "0.1.0"