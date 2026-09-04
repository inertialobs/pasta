# PASTA — Parallel Astrodynamic Solver for Trajectory Analysis

[![License](https://img.shields.io/badge/License-GPL--v3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0.txt) [![Python](https://img.shields.io/badge/python-3.14-blue)](https://www.python.org/) [![Platform](https://img.shields.io/badge/platform-Windows%20x64-lightgrey)](https://www.microsoft.com/en-us/windows)

A configuration-driven MGA-1DSM gravity-assist trajectory optimization tool with a browser GUI: configure → parallel search → 3D visualization, all in one page — no Python scripting required.

The name stands for *Parallel Astrodynamic Solver for Trajectory Analysis* — complex transfer trajectories look like tangled spaghetti.

> 中文说明见 [README_zh.md](README_zh.md) · Chinese docs in `README_zh.md`

- **Frontend**：Local Web page (http://127.0.0.1:8765)
- **Backend**：Flask + JobManager + per-job subprocess + multiprocessing parallel search (scan → refine → ballistic seed → wide/tight compress → pick best)
- **Engine**：pykep (mga_1dsm / lambert) + pygmo


## Features

- **Configuration as script**：a single web page completes "configure → compute → visualize"; the generated config is itself a reproducible full task script (JSON).
- **Six-stage parallel pipeline**：`scan → refine → ballistic seed → wide J→U compress → tight-frontier compress → pick best`；each stage independently toggleable (computation presets).
- **Built-in warm start (WARM_X)**：injects a previously-found near-optimal solution as a seed, giving a second-level baseline.
- **Fixed random seeds**：fixed seeds + pinned numeric library versions → bit-identical reproducibility (same config, same result on repeated runs).
- **Task / computation presets separated**：task presets hold only mission settings; computation presets hold only engine settings; both customizable and exportable to `presets/`.
- **3D visualization**：Plotly 3D trajectory + static PNG + structured `result.json`.

## Installation

Three ways to get PASTA:

### 1. Packaged binary (recommended)
Grab the binary file from the [release page](https://github.com/inertialobs/pasta/releases) and it works out of the box.

### 2. Source via pip
Requires Windows + Python 3.14. Notice that `pykep`/`pygmo` have no official Windows wheels on PyPI, so install them from the prebuilt wheel repository first:
- Download the matching `pykep` and `pygmo` `.whl` from [inertialobs/pykep-pygmo-win-wheels/releases](https://github.com/inertialobs/pykep-pygmo-win-wheels/releases)
- Then:
  ```bash
  pip install <path>\pygmo-*.whl
  pip install <path>\pykep-*.whl
  pip install -r requirements.txt
  python main.py        # default http://127.0.0.1:8765
  ```

### 3. Source via conda
Install `pykep`/`pygmo` through conda following the [official pykep docs](https://esa.github.io/pykep/), then run from the project dir:
```bash
python main.py        # default http://127.0.0.1:8765
```

### CLI examples
```bash
python main.py --port 0                              # random free port
python main.py --host 0.0.0.0 --port 8765            # expose to LAN (secure intranet only)
```

### Build the binary yourself (optional)
```bash
pip install -r requirements.txt
pip install pyinstaller
pyinstaller build.spec           # output dist\pasta\pasta.exe (onedir)
```

## Usage

1. **Mission config**：task name on its own row；planet-sequence nodes addable/removable；per-leg TOF bounds；objective & constraints (min_tof / min_dsm / custom weights, DSM limit, launch/arrival v∞, eta, rp upper bound, frontier penalty)；launch windows addable/removable (multiple epochs).
2. **Computation config**：4 pipeline stage toggles, smoke/full mode, worker count, search step, scan/refine keep counts, warm-start toggle.
3. **Submit** → automatically queued (at most 1 running job; the rest queue).
4. **Result card**：total TOF / total DSM / C3, per-leg details (flyby rp, DSM location), 3D plot + static image.
5. **Job management**：running / queued / cancel / delete；closing the tab keeps the job running in the background；top-right「⏹」stops the backend.
6. **Presets**：task-preset + computation-preset dropdowns；loading fills the form (with load feedback)；「save as…」exports to `presets/*.json`.
7. **System settings**：port, single instance, expose to LAN, auto-open browser.

## Architecture

```
Browser (Flask web UI)
   └─ JobManager ── Popen subprocess: <pasta.exe> --cli --config ... --outdir ...
        └─ ProcessPoolExecutor(jobs=N) parallel search (scan/refine/compress)
```

- **Entry** `main.py`：starts Flask; the `--cli` subprocess mode strips the flag and forwards to the computation entry; the entry calls `multiprocessing.freeze_support()` (required in PyInstaller frozen builds or the pool workers crash with BrokenProcessPool).
- **Computation entry** `orbcalc/run_cli.py`：loads config → runs the 6 stages → writes artifacts; the error path releases all multiprocessing children in `finally`.
- **Artifacts**（per-job dir `runs/<job>/`）：`config.json`、`log.txt`、`result.json`、`plot.json`、`best_x.npy`、`trajectory.png`.
- **Config-driven**：`orbcalc/config.py` `TrajConfig` carries every task parameter (defaults aligned item-by-item with the reference script `temp/EVVEJU_TOF_1DSM_mp.py`).
- **System config** `orbitcalculator.sys.json`：`host` / `port` / `single_instance` / `open_browser` / `show_lan_warning`；CLI flags override the file.
<!-- 
## Built-in Presets

| Task Preset | Description |
|---|---|
| EVVEJU (default, with warm start) | E→V→V→E→J→Uranus，era 2029-2033 + 2017-2021 |
| EVVEJS Cassini (1997-10) | E→V→V→E→J→Saturn (the real Cassini sequence), era 1997, TOF from actual legs |

| Computation Preset | Description |
|---|---|
| Default full pipeline (8 workers) | complete 6 stages |
| Quick smoke | reduced parameters, minute-level validation |
| Scan + refine only | seed/compress off |
| Evaluate WARM only (seconds) | evaluate the built-in warm-start solution only, seconds-level result/image | -->

## Layout

```
main.py                 Web/CLI launcher
build.spec              PyInstaller packaging config
requirements(.dev).txt  run / build dependencies
orbcalc/                engine layer (config / udp / stages / engines / run_cli / sysconfig)
webapp/                 Flask backend + frontend (templates + static)
presets/                user-exported presets
runs/<job>/             job artifacts
```

### License: [GPL-3.0](https://www.gnu.org/licenses/gpl-3.0.txt)

![GPLv3-logo](https://www.gnu.org/graphics/gplv3-with-text-136x68.png)