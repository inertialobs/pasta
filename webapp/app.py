# -*- coding: utf-8 -*-
"""
Flask 应用 + JobManager: 轨道优化任务的 Web 前端后端。

架构:
    前端 (浏览器) <-> Flask (本机 127.0.0.1) <-> JobManager <-> 子进程
        python -m orbcalc.run_cli --config ... --outdir ...

- 每个任务 = 一个独立子进程 (崩溃隔离, 可硬取消, pykep 重加载不进 Flask 进程)。
- 任务产物 (log.txt / result.json / plot.json / best_x.npy / trajectory.png) 落在 runs/<job_id>/。
- 并发: 同时最多 1 个运行中任务, 其余排队; 监督线程自动接力。
- 仅绑定 127.0.0.1, 无鉴权 (本地单用户工具)。
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

from orbcalc import slog
from orbcalc.config import TrajConfig, PRESETS, sanitize_name
from orbcalc.sysconfig import SysConfig, load_sysconfig, save_sysconfig, sysconfig_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# PyInstaller 冻结时: webapp 资源 (templates/static) 在 _MEIPASS 解包根;
# runs/presets 仍是用户数据目录, 用项目根 (冻结后 exe 所在目录旁)。
if getattr(sys, "frozen", False):
    RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
else:
    RESOURCE_ROOT = PROJECT_ROOT

RUNS_DIR = PROJECT_ROOT / "runs"
PRESETS_DIR = PROJECT_ROOT / "presets"
for _d in (RUNS_DIR, PRESETS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _kill_tree(pid: int) -> None:
    """整棵进程树终止 (含 multiprocessing 池 workers 等子进程).
    Windows 用 taskkill /T /F; 其他平台 SIGTERM 到进程组."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=10)
            return
        except Exception:
            pass
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


class JobManager:
    """任务生命周期管理: submit / poll / cancel / queue / artifacts."""

    MAX_RUNNING = 1

    def __init__(self, runs_dir: Path):
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict] = {}
        self._queue: list[str] = []
        self._active: str | None = None
        self._lock = threading.Lock()
        self._sup = threading.Thread(target=self._supervise, daemon=True)
        self._sup.start()

    # ------------------------------------------------------------------
    def submit(self, config_dict: dict, jobs_override: int | None = None) -> str:
        cfg = TrajConfig.from_dict(config_dict)
        if jobs_override and jobs_override > 0:
            cfg.jobs = int(jobs_override)
        cfg.validate()
        jid = time.strftime("%Y%m%d_%H%M%S") + "_" + sanitize_name(cfg.name)
        d = self.runs_dir / jid
        d.mkdir(parents=True, exist_ok=True)
        cfg.to_json(str(d / "config.json"))
        with open(d / "request.json", "w", encoding="utf-8") as f:
            json.dump(config_dict, f, ensure_ascii=False, indent=2)
        with self._lock:
            self._jobs[jid] = {
                "job_id": jid,
                "name": cfg.name,
                "created": time.time(),
                "status": "queued" if self._active else "starting",
                "jobs": cfg.jobs,
                "dir": str(d),
            }
            if self._active is None:
                self._start(jid)
            else:
                self._queue.append(jid)
        slog.inf(f"[job] submit jid={jid} name={cfg.name} jobs={cfg.jobs} seq={cfg.seq}")
        return jid

    def _start(self, jid: str) -> None:
        job = self._jobs[jid]
        d = Path(job["dir"])
        job["status"] = "running"
        job["started"] = time.time()
        log_f = open(d / "log.txt", "a", encoding="utf-8", buffering=1)
        log_f.write(f"=== job {jid} started {time.time():.0f} ===\n")
        if getattr(sys, "frozen", False):
            # 冻结版无 python 解释器: 子进程 = 当前 exe 的 --cli 模式
            cmd = [sys.executable, "--cli",
                   "--config", str(d / "config.json"),
                   "--jobs", str(job["jobs"]),
                   "--outdir", str(d)]
        else:
            cmd = [sys.executable, "-m", "orbcalc.run_cli",
                   "--config", str(d / "config.json"),
                   "--jobs", str(job["jobs"]),
                   "--outdir", str(d)]
        job["log_f"] = log_f
        job["proc"] = subprocess.Popen(
            cmd, cwd=str(PROJECT_ROOT),
            stdout=log_f, stderr=log_f,
            creationflags=CREATE_NO_WINDOW,
        )
        slog.inf(f"[job] start jid={jid} cmd={' '.join(cmd)}")

    def _maybe_next(self) -> None:
        with self._lock:
            if self._active is None and self._queue:
                nxt = self._queue.pop(0)
                self._active = nxt
                self._jobs[nxt]["status"] = "starting"
                self._start(nxt)

    def _supervise(self) -> None:
        while True:
            try:
                with self._lock:
                    active = self._active
                if active:
                    self._poll(active)
                self._maybe_next()
            except Exception:
                pass
            time.sleep(1.5)

    # ------------------------------------------------------------------
    def _poll(self, jid: str) -> None:
        job = self._jobs[jid]
        proc = job.get("proc")
        if proc is None:
            return
        rc = proc.poll()
        if rc is None:
            return
        # 任务已结束: 关闭日志句柄 (之后不再 flush/close)
        log_f = job.get("log_f")
        if log_f is not None:
            log_f.flush()
            log_f.close()
            job["log_f"] = None
        result_path = Path(job["dir"]) / "result.json"
        if rc == 0 and result_path.exists():
            job["status"] = "done"
        elif rc == 130:
            job["status"] = "cancelled"
        else:
            job["status"] = "failed"
        job["rc"] = rc
        job["finished"] = time.time()
        slog.inf(f"[job] end jid={jid} status={job['status']} rc={rc} "
                 f"elapsed={job['finished'] - job.get('started', job['finished']):.1f}s")
        with self._lock:
            if self._active == jid:
                self._active = None

    # ------------------------------------------------------------------
    @staticmethod
    def _read_tail(path: Path, n: int = 250) -> list[str]:
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return []
        return lines[-n:]

    @staticmethod
    def _disk_status(d: Path) -> str:
        if (d / "cancelled.txt").exists():
            return "cancelled"
        if (d / "result.json").exists():
            return "done"
        return "failed"

    def _info_from_disk(self, jid: str) -> dict | None:
        """重启后从 runs/<jid>/ 重建任务信息 (历史/持久化)."""
        d = self.runs_dir / jid
        if not d.is_dir():
            return None
        cfg = {}
        rf = d / "request.json"
        if rf.exists():
            try:
                cfg = json.loads(rf.read_text(encoding="utf-8")) or {}
            except Exception:
                cfg = {}
        # request.json 内容就是扁平 config (submit 时 json.dump(config_dict))
        log_lines = self._read_tail(d / "log.txt")
        return {
            "job_id": jid,
            "name": cfg.get("name", jid),
            "created": self._dir_mtime(d),
            "status": self._disk_status(d),
            "jobs": cfg.get("jobs"),
            "dir": str(d),
            "elapsed_s": None,
            "has_result": (d / "result.json").exists(),
            "log_tail": log_lines,
            "log_len": len(log_lines) if (d / "log.txt").exists() else 0,
        }

    @staticmethod
    def _dir_mtime(d: Path) -> float:
        try:
            return d.stat().st_mtime
        except OSError:
            return 0.0

    # ------------------------------------------------------------------
    def get(self, jid: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(jid)
        if job is None:
            return self._info_from_disk(jid)
        self._poll(jid)
        d = Path(job["dir"])
        out = {k: v for k, v in job.items()
               if k not in ("proc", "log_f")}
        out["elapsed_s"] = None
        if "started" in out:
            end = out.get("finished", time.time())
            out["elapsed_s"] = round(end - out["started"], 1)
        result_path = d / "result.json"
        out["has_result"] = result_path.exists()
        log_path = d / "log.txt"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            out["log_tail"] = lines[-250:]
            out["log_len"] = len(lines)
        else:
            out["log_tail"] = []
            out["log_len"] = 0
        return out

    def list(self) -> list[dict]:
        """内存中的任务 + 磁盘上 (重启后) 的历史任务."""
        with self._lock:
            ids = list(self._jobs.keys())
        items = []
        for jid in ids:
            info = self.get(jid)
            if info:
                items.append(info)
        seen = set(ids)
        if self.runs_dir.exists():
            for d in sorted(self.runs_dir.iterdir(), key=lambda p: p.name, reverse=True):
                if not d.is_dir() or d.name in seen:
                    continue
                info = self._info_from_disk(d.name)
                if info:
                    items.append(info)
                    seen.add(d.name)
        items.sort(key=lambda i: i["created"], reverse=True)
        return items

    def cancel(self, jid: str) -> bool:
        with self._lock:
            job = self._jobs.get(jid)
        if job is None:
            return False
        proc = job.get("proc")
        if proc is not None and proc.poll() is None:
            _kill_tree(proc.pid)   # 整树杀: 连同 multiprocessing 池 workers, 防孤儿
        job["status"] = "cancelled"
        try:
            (Path(job["dir"]) / "cancelled.txt").touch()
        except OSError:
            pass
        slog.inf(f"[job] cancel jid={jid}")
        return True

    def delete(self, jid: str) -> bool:
        """从列表与磁盘彻底删除任务 (运行中先终止, 排队中移除).
        磁盘历史任务 (重启后不在内存 _jobs) 也能删: 按 runs/<jid> 目录删除."""
        with self._lock:
            job = self._jobs.get(jid)
            if job is not None:
                proc = job.get("proc")
                if proc is not None and proc.poll() is None:
                    _kill_tree(proc.pid)   # 整树杀: 连同 multiprocessing 池 workers
                if jid in self._queue:
                    self._queue.remove(jid)
                if self._active == jid:
                    self._active = None
                dirpath = Path(job["dir"])
                del self._jobs[jid]
            else:
                d = self.runs_dir / jid
                if not d.is_dir():
                    return False   # 内存与磁盘都不存在 -> 404
                dirpath = d
        import shutil
        shutil.rmtree(dirpath, ignore_errors=True)   # 连 log/result/plot 等产物一并删除
        slog.inf(f"[job] delete jid={jid}")
        return True


# 预设字段分组: 任务配置 (轨迹) / 计算配置 (流水线)
TRAJ_FIELDS = {"name", "seq", "safe_radius", "tof_bounds", "vinf_bounds_kmps",
               "eta_bounds", "rp_ub", "eras", "objective",
               "objective_weights", "dsm_limit_ms", "penalty", "frontier_penalty",
               "wl", "vinf_launch_limit_ms", "wa", "vinf_arrival_limit_ms", "warm_x"}
COMP_FIELDS = {"name", "run_scan", "run_seed", "run_compress", "run_frontier",
               "scan_keep", "refine_keep", "jobs", "smoke", "era_step_d"}  # 搜索步进(天)归计算配置


def _subdict(d: dict, fields: set) -> dict:
    return {k: v for k, v in d.items() if k in fields}


def _builtin_comp_presets() -> dict:
    """内置计算配置预设 (流水线开关/保留个数/进程数/冒烟)."""
    full = _subdict(TrajConfig().to_dict(), COMP_FIELDS)          # 默认全流水线
    smoke = dict(full); smoke["smoke"] = True
    scan_refine = dict(full); scan_refine["run_seed"] = False
    scan_refine["run_compress"] = False; scan_refine["run_frontier"] = False
    ev = dict(full); ev["run_scan"] = False; ev["run_seed"] = False
    ev["run_compress"] = False; ev["run_frontier"] = False
    return {
        "默认全流水线 (8 进程)": full,
        "冒烟快速 (smoke)": smoke,
        "仅扫描+细化": scan_refine,
        "仅评估 WARM (秒级)": ev,
    }


def load_custom_presets() -> tuple[dict, dict]:
    """从 presets/*.json 载入用户预设: 返回 (traj 视图, comp 视图).

    - comp_*.json -> 计算配置视图
    - 其他 (含旧格式完整配置) -> 任务配置视图 (取轨迹字段子集)
    """
    traj, comp = {}, {}
    if PRESETS_DIR.exists():
        for f in sorted(PRESETS_DIR.glob("*.json")):
            try:
                cfg = TrajConfig.from_json(path=str(f))
            except Exception:
                continue  # 损坏的预设文件跳过
            if f.name.startswith("comp_"):
                comp[cfg.name] = _subdict(cfg.to_dict(), COMP_FIELDS)
            else:
                traj[cfg.name] = _subdict(cfg.to_dict(), TRAJ_FIELDS)
    return traj, comp


def create_app() -> Flask:
    app = Flask(__name__,
                template_folder=str(RESOURCE_ROOT / "webapp" / "templates"),
                static_folder=str(RESOURCE_ROOT / "webapp" / "static"))
    app.config["DEFAULT_JOBS"] = None  # main.py --jobs 可设置默认并行度
    app.config["SYS"] = load_sysconfig()   # 系统配置 (host/port/单实例)
    jm = JobManager(RUNS_DIR)

    # ------------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        import pykep
        syscfg = app.config["SYS"]
        return jsonify({"ok": True, "pykep": pykep.__version__,
                        "runs_dir": str(RUNS_DIR),
                        "host": syscfg.host, "port": syscfg.port,
                        "lan": syscfg.is_lan,
                        "single_instance": syscfg.single_instance})

    @app.get("/api/sysconfig")
    def get_sysconfig():
        syscfg = app.config["SYS"]
        out = syscfg.to_dict()
        out["is_lan"] = syscfg.is_lan
        out["path"] = sysconfig_path()
        return jsonify(out)

    @app.post("/api/sysconfig")
    def set_sysconfig():
        """保存系统配置 (host/port/单实例…), 重启后生效."""
        body = request.get_json(force=True, silent=True) or {}
        syscfg = app.config["SYS"]
        new = syscfg.from_dict({**syscfg.to_dict(), **body})
        try:
            new.validate()
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        path = save_sysconfig(new)
        app.config["SYS"] = new
        return jsonify({"saved": True, "path": path,
                        "restart_required": True}), 200

    @app.post("/api/shutdown")
    def shutdown():
        """优雅退出后端: 终止活跃任务 -> 删锁文件 -> 退出进程."""
        slog.inf("[web] shutdown 请求: 取消活跃任务并退出")
        try:
            if jm._active:
                jm.cancel(jm._active)
        except Exception:
            pass
        try:
            for jid in list(jm._jobs):
                jm.cancel(jid)
        except Exception:
            pass
        try:
            lock = PROJECT_ROOT / "orbitcalculator.lock.json"
            if lock.exists():
                lock.unlink()
        except Exception:
            pass
        threading.Timer(0.4, lambda: os._exit(0)).start()
        return jsonify({"bye": True})

    @app.get("/api/presets")
    def presets():
        """分组返回: {\"traj\": {...}, \"comp\": {...}}"""
        traj = {name: _subdict(fn().to_dict(), TRAJ_FIELDS)
                for name, fn in PRESETS.items()}
        comp = _builtin_comp_presets()
        ctraj, ccomp = load_custom_presets()
        traj.update(ctraj)
        comp.update(ccomp)
        return jsonify({"traj": traj, "comp": comp})

    @app.post("/api/presets")
    def save_preset():
        """保存自定义预设: {name, config, kind:\"traj\"|\"comp\"} -> presets/<prefix>_<名>.json"""
        body = request.get_json(force=True, silent=True) or {}
        name = str(body.get("name") or "").strip()
        cfg_dict = body.get("config")
        kind = str(body.get("kind") or "traj").strip()
        if kind not in ("traj", "comp"):
            return jsonify({"error": "kind 应为 traj 或 comp"}), 400
        if not name or not isinstance(cfg_dict, dict):
            return jsonify({"error": "need name and config"}), 400
        try:
            cfg = TrajConfig.from_dict(cfg_dict)
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        cfg.name = name
        fname = ("comp_" if kind == "comp" else "traj_") + sanitize_name(name) + ".json"
        cfg.to_json(str(PRESETS_DIR / fname))
        return jsonify({"saved": cfg.name, "file": fname, "kind": kind}), 201

    @app.post("/api/jobs")
    def create_job():
        body = request.get_json(force=True, silent=True) or {}
        cfg = body.get("config")
        if not cfg:
            return jsonify({"error": "missing config"}), 400
        jobs_override = body.get("jobs") or app.config.get("DEFAULT_JOBS")
        try:
            jid = jm.submit(cfg, jobs_override=jobs_override)
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"job_id": jid, "status": jm.get(jid)["status"]}), 201

    @app.get("/api/jobs")
    def list_jobs():
        return jsonify(jm.list())

    @app.get("/api/jobs/<jid>")
    def job_status(jid):
        info = jm.get(jid)
        if info is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(info)

    @app.post("/api/jobs/<jid>/cancel")
    def cancel_job(jid):
        ok = jm.cancel(jid)
        if not ok:
            return jsonify({"error": "not found"}), 404
        return jsonify({"job_id": jid, "cancelled": True})

    @app.delete("/api/jobs/<jid>")
    def delete_job(jid):
        ok = jm.delete(jid)
        if not ok:
            return jsonify({"error": "not found"}), 404
        return jsonify({"job_id": jid, "deleted": True})

    # --- 产物下载 ---
    @app.get("/api/jobs/<jid>/<path:artifact>")
    def job_artifact(jid, artifact):
        if artifact not in ("result.json", "plot.json", "best_x.npy",
                            "trajectory.png", "config.json", "log.txt"):
            return jsonify({"error": "bad artifact"}), 400
        d = Path(RUNS_DIR) / jid
        f = d / artifact
        if not f.exists():
            return jsonify({"error": "artifact not ready"}), 404
        return send_from_directory(d, artifact, as_attachment=False)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8765, threaded=True, debug=False)