# PASTA — Parallel Astrodynamic Solver for Trajectory Analysis

[![License](https://img.shields.io/badge/License-GPL--v3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0.txt) [![Python](https://img.shields.io/badge/python-3.14-blue)](https://www.python.org/) [![Platform](https://img.shields.io/badge/platform-Windows%20x64-lightgrey)](https://www.microsoft.com/en-us/windows)
并行天体动力轨道分析求解器：配置驱动的 MGA-1DSM 弹弓轨道优化 GUI 工具。

- 前端：本地 Web 界面 (`http://127.0.0.1:8765`)
- 后端：`Flask + multiprocessing` 并行搜索 (扫描 → 细化 → 播种 → 压缩 → 前沿)
- 引擎：`pykep (mga_1dsm / lambert) + pygmo`


> English docs → [README.md](README.md)


## 特性

- **配置即脚本**：一个网页完成「配置 → 计算 → 可视化」，表单生成的配置本身就是可复现的完整任务脚本（JSON）。
- **六阶段并行流水线**：`扫描 → 细化 → 弹道播种 → 宽 J→U 压缩 → 紧前沿压缩 → 精选`，每阶段独立可开关（计算预设）。
- **内置热启动 (WARM_X)**：把已搜到的已知最优解作为种子注入流水线，秒级出基线。
- **固定随机种子**：同版本数值库 + 固定种子 → 逐位可复现（同配置两次运行结果一致）。
- **任务/计算预设分离**：任务预设只含任务设置；计算预设只含计算参数；可自定义、可导出到 `presets/`。
- **3D 可视化**：Plotly 3D 轨道图 + 静态 PNG + 结构化 `result.json`。

## 安装

有三种安装方式：

### 1. 预编译二进制文件（推荐）
从 [release 页面](https://github.com/inertialobs/pasta/releases)下载开箱即用的二进制包

### 2. pip（源码运行）
需要 Windows + Python 3.14。注意：PyPI 上没有 pykep / pygmo 的官方 Windows wheel，需先从预编译 wheel 仓库安装：
- 从 [inertialobs/pykep-pygmo-win-wheels/releases](https://github.com/inertialobs/pykep-pygmo-win-wheels/releases) 下载对应 `pykep` 和 `pygmo` 的 `.whl`
- 然后：
  ```bash
  pip install <path>\pygmo-*.whl
  pip install <path>\pykep-*.whl
  pip install -r requirements.txt
  python main.py        # 默认 http://127.0.0.1:8765
  ```

### 3. conda（源码运行）
按 [pykep 官方文档](https://esa.github.io/pykep/) 通过 conda 安装 `pykep` / `pygmo`，再到项目目录运行：
```bash
python main.py               # 默认 http://127.0.0.1:8765
```

### 命令行示例
```bash
python main.py --port 0                                # 随机空闲端口
python main.py --host 0.0.0.0 --port 8765              # 开放局域网（仅安全内网）
```

### 自行打包（可选发布）
```bash
pip install -r requirements.txt
pip install pyinstaller
pyinstaller build.spec       # 产物 dist\pasta\pasta.exe
```

## 使用

1. **任务配置**：任务名单独一行；行星序列节点可增删；每腿 TOF 边界；目标与约束（min_tof / min_dsm / 自定义权重、DSM 上限、发射/到达 v∞、eta、rp 上界、前沿罚）；发射窗口可增删（多 era）。
2. **计算配置**：流水线 4 阶段开关、冒烟/完整模式、并行进程数、搜索步进、扫描/细化保留数、内置热启动开关。
3. **提交计算** → 自动排队执行（同时最多 1 个任务，其余排队）。
4. **结果卡**：总飞行时间 / 总 DSM / C3，逐腿详情（飞掠 rp、DSM 位置），3D 轨道图 + 静态图。
5. **任务管理**：运行 / 排队 / 取消 / 删除；关闭标签页任务仍在后台继续；右上角「⏹ 终止程序」停止后端。
6. **预设**：任务预设 + 计算预设下拉，载入即填表单（带加载反馈），「存为…」导出到 `presets/*.json`。
7. **系统设置**：端口、单实例、开放局域网、自动打开浏览器。

## 架构

```
浏览器 (Flask Web 界面)
   └─ JobManager ── Popen 子进程: <pasta.exe> --cli --config ... --outdir ...
        └─ ProcessPoolExecutor(jobs=N) 并行搜索 (扫描/细化/压缩)
```

- **主入口** `main.py`：启动 Flask；`--cli` 子进程模式剥掉标记转发给计算入口；入口调用 `multiprocessing.freeze_support()`（PyInstaller 冻结版必需，否则池 worker 崩溃 BrokenProcessPool）。
- **计算入口** `orbcalc/run_cli.py`：加载配置 → 跑 6 阶段 → 写产物；异常路径在 `finally` 中释放所有 multiprocessing 子进程。
- **产物**（每任务目录 `runs/<job>/`）：`config.json`、`log.txt`、`result.json`、`plot.json`、`best_x.npy`、`trajectory.png`。
- **配置驱动**：`orbcalc/config.py` 的 `TrajConfig` 承载全部任务参数（默认值逐项对齐参考脚本 `temp/EVVEJU_TOF_1DSM_mp.py`）。
- **系统配置** `orbitcalculator.sys.json`：`host` / `port` / `single_instance` / `open_browser` / `show_lan_warning`；命令行参数优先于文件。
<!-- 
## 🔧 内置预设

| 任务预设 | 说明 |
|---|---|
| EVVEJU（默认，含热启动） | E→V→V→E→J→Uranus，era 2029-2033 + 2017-2021 |
| EVVEJS 卡西尼号（1997-10） | E→V→V→E→J→Saturn卡西尼号真实序列，era 1997，TOF 按实测行程 |

| 计算预设 | 说明 |
|---|---|
| 默认全流水线（8 进程） | 完整 6 阶段 |
| 冒烟快速（smoke） | 小化参数，分钟级冒烟验证 |
| 仅扫描+细化 | 关掉播种/压缩 |
| 仅评估 WARM（秒级） | 只评估内置热启动解，秒级出结果/出图 | -->

## 目录结构

```
main.py                 Web/CLI 启动器
build.spec              PyInstaller 打包配置
requirements(.dev).txt  运行/打包依赖
orbcalc/                引擎层 (config / udp / stages / engines / run_cli / sysconfig)
webapp/                 Flask 后端 + 前端 (templates + static)
presets/                用户导出的预设
runs/<job>/             任务产物
```


### license: [GPL-3.0 License](https://www.gnu.org/licenses/gpl-3.0.txt)

![GPLv3-logo](https://www.gnu.org/graphics/gplv3-with-text-136x68.png)