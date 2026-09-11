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
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

from orbcalc import slog
from orbcalc.config import TrajConfig, sanitize_name
from settings import CONFIG_FILE, settings

# 运行根目录: main.py 启动时已 chdir 到此 (源码=项目根, 冻结=exe 目录)。
# 资源 (webapp/presets) 与用户数据 (runs/presets) 同根。
ROOT = Path.cwd()
RUNS_DIR = ROOT / "runs"
PRESETS_DIR = ROOT / "presets"          # 用户预设 (可写)
for _d in (RUNS_DIR, PRESETS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _kill_tree(pid: int) -> None:
    """整棵进程树终止 (含 multiprocessing 池 workers 等子进程).
    Windows 用 taskkill /T /F; 其他平台向子进程组发 SIGTERM
    (子进程以 start_new_session=True 启动, 故其 pgid == pid)。"""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=10)
            return
        except Exception:
            pass
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
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
        with self._lock:
            # 同秒同名也要唯一: 目录已存在/内存已有则追加序号 (加锁内完成, 防并发)
            base = time.strftime("%Y%m%d_%H%M%S") + "_" + sanitize_name(cfg.name)
            jid = base
            n = 2
            while jid in self._jobs or (self.runs_dir / jid).exists():
                jid = f"{base}_{n}"
                n += 1
            d = self.runs_dir / jid
            d.mkdir(parents=True, exist_ok=True)
            cfg.to_json(str(d / "config.json"))
            with open(d / "request.json", "w", encoding="utf-8") as f:
                json.dump(config_dict, f, ensure_ascii=False, indent=2)
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
        stale = d / "cancelled.txt"
        if stale.exists():
            try:
                stale.unlink()   # 清掉复用目录时的旧取消标志
            except OSError:
                pass
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
            cmd, stdout=log_f, stderr=log_f,
            creationflags=CREATE_NO_WINDOW,
            start_new_session=(os.name != "nt"),   # POSIX: 独立进程组, 便于整树杀
        )
        slog.inf(f"[job] start jid={jid} cmd={' '.join(cmd)}")

    def _maybe_next(self) -> None:
        with self._lock:
            if self._active is not None:
                return
            # 跳过已取消的排队项 (防御: 正常应在 cancel 时出队)
            nxt = None
            while self._queue:
                cand = self._queue.pop(0)
                if not self._jobs[cand].get("cancelled"):
                    nxt = cand
                    break
            if nxt is not None:
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
        job = self._jobs.get(jid)
        if job is None or job.get("finished") is not None:
            return   # 已处理过终结状态, 不再重复 poll/改 elapsed/写日志
        proc = job.get("proc")
        if proc is None:
            return
        rc = proc.poll()
        if rc is None:
            return
        # 任务已结束: 关闭日志句柄 (之后不再 flush/close; get/supervise 并发
        # 进入时句柄可能已被对方关闭, 容错跳过即可)
        log_f = job.get("log_f")
        if log_f is not None:
            job["log_f"] = None
            try:
                log_f.flush()
                log_f.close()
            except Exception:
                pass
        result_path = Path(job["dir"]) / "result.json"
        if rc == 0 and result_path.exists():
            # 成功优先于 cancelled 标志: cancel 可能在进程收尾后才到达
            job["status"] = "done"
        elif job.get("cancelled"):
            # cancel 打的标志优先: taskkill /F 退出码非 130, 不能只看 rc
            job["status"] = "cancelled"
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
        # result.json 的 status 字段最权威; cancelled.txt 仅在无结果时作依据
        rf = d / "result.json"
        if rf.exists():
            try:
                st = json.loads(rf.read_text(encoding="utf-8")).get("status")
            except Exception:
                return "failed"   # 结果文件损坏/只写了一半
            if st == "cancelled":
                return "cancelled"
            if st == "error":
                return "failed"
            return "done"
        if (d / "cancelled.txt").exists():
            return "cancelled"
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
        if job.get("status") in ("done", "failed", "cancelled"):
            return True   # 已终结: 不改写状态, 也不写 cancelled.txt
        proc = job.get("proc")
        running = proc is not None and proc.poll() is None
        if not running and (Path(job["dir"]) / "result.json").exists():
            # 进程已退出且成功产出结果: 视为成功, 不标 cancelled
            job["status"] = "done"
            job["finished"] = job.get("finished") or time.time()
            return True
        if running:
            _kill_tree(proc.pid)   # 整树杀: 连同 multiprocessing 池 workers, 防孤儿
        job["cancelled"] = True   # _poll 据此判 cancelled (taskkill 退出码非 130)
        with self._lock:
            # 排队中: 必须出队, 否则 _maybe_next 仍会把它拉起
            if jid in self._queue:
                self._queue.remove(jid)
            if self._active == jid and (proc is None or proc.poll() is not None):
                self._active = None
        job["status"] = "cancelled"
        try:
            (Path(job["dir"]) / "cancelled.txt").touch()
        except OSError:
            pass
        slog.inf(f"[job] cancel jid={jid}")
        return True

    def cancel_pending_or_running(self) -> None:
        """仅取消未终结的任务 (shutdown 用; 已 done/failed/cancelled 的历史不动)."""
        with self._lock:
            ids = [j for j, job in self._jobs.items()
                   if job.get("status") in ("running", "starting", "queued")]
        for jid in ids:
            try:
                self.cancel(jid)
            except Exception:
                pass

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


# 预设字段分组: 任务配置 (轨迹)
TRAJ_FIELDS = {"name", "seq", "safe_radius", "tof_bounds", "vinf_bounds_kmps",
               "eta_bounds", "rp_ub", "eras", "objective",
               "objective_weights", "dsm_limit_ms", "penalty", "frontier_penalty",
               "wl", "vinf_launch_limit_ms", "wa", "vinf_arrival_limit_ms"}

# 全局配置 (settings) 字段分组: 系统 / 计算
SYS_FIELDS = {"host", "port", "open_browser"}
COMPUTE_FIELDS = {"run_scan", "run_seed", "run_compress", "run_frontier",
                  "scan_keep", "refine_keep", "era_step_d", "jobs"}


def _subdict(d: dict, fields: set) -> dict:
    return {k: v for k, v in d.items() if k in fields}


def _load_preset_dir(dirpath: Path, traj: dict) -> None:
    """扫描目录里的任务预设 JSON 并就地并入 traj.

    - 可选 "title" 字段作显示名 (TrajConfig 会忽略未知字段); 缺省用 name
    - comp_*.json 为已废弃的旧计算预设, 直接忽略
    """
    if not dirpath.is_dir():
        return
    for f in sorted(dirpath.glob("*.json")):
        if f.name.startswith("comp_"):
            continue  # 遗留计算预设 (已废弃): 计算配置现由 /api/compcfg 全局管理
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            cfg = TrajConfig.from_dict(raw)
        except Exception:
            continue  # 损坏的预设文件跳过
        title = raw.get("title") or cfg.name
        traj[title] = _subdict(cfg.to_dict(), TRAJ_FIELDS)


def load_presets() -> dict:
    """载入任务预设: 返回 {显示名: 轨迹字段子集}.

    预设与运行资源同根 (presets/), 随包分发、可被用户覆盖。
    """
    traj = {}
    _load_preset_dir(PRESETS_DIR, traj)
    return traj


def create_app() -> Flask:
    app = Flask(__name__,
                template_folder=str(ROOT / "webapp" / "templates"),
                static_folder=str(ROOT / "webapp" / "static"))
    app.config["DEFAULT_JOBS"] = None  # main.py --jobs 可设置默认并行度
    jm = JobManager(RUNS_DIR)

    # ------------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        import pykep
        host = settings["host"]
        return jsonify({"ok": True, "pykep": pykep.__version__,
                        "runs_dir": str(RUNS_DIR),
                        "host": host, "port": settings["port"],
                        "lan": host in ("0.0.0.0", "::")})

    @app.get("/api/sysconfig")
    def get_sysconfig():
        out = {k: settings[k] for k in SYS_FIELDS}
        out["is_lan"] = settings["host"] in ("0.0.0.0", "::")
        out["path"] = os.path.abspath(CONFIG_FILE)
        return jsonify(out)

    @app.post("/api/sysconfig")
    def set_sysconfig():
        """保存系统配置 (host/port/浏览器…), 重启后生效."""
        body = request.get_json(force=True, silent=True) or {}
        try:
            settings.update(body)
            settings.save_file()
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"saved": True, "path": os.path.abspath(CONFIG_FILE),
                        "restart_required": True}), 200

    @app.post("/api/shutdown")
    def shutdown():
        """优雅退出后端: 终止未完成任务 -> 退出进程."""
        slog.inf("[web] shutdown 请求: 取消活跃任务并退出")
        try:
            jm.cancel_pending_or_running()
        except Exception:
            pass
        threading.Timer(0.4, lambda: os._exit(0)).start()
        return jsonify({"bye": True})

    @app.get("/api/presets")
    def presets():
        """任务预设: {显示名: 轨迹字段子集}"""
        return jsonify(load_presets())

    @app.post("/api/presets")
    def save_preset():
        """保存任务预设: {name, config} -> presets/traj_<名>.json"""
        body = request.get_json(force=True, silent=True) or {}
        name = str(body.get("name") or "").strip()
        cfg_dict = body.get("config")
        if not name or not isinstance(cfg_dict, dict):
            return jsonify({"error": "need name and config"}), 400
        try:
            cfg = TrajConfig.from_dict(cfg_dict)
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        cfg.name = name
        fname = "traj_" + sanitize_name(name) + ".json"
        cfg.to_json(str(PRESETS_DIR / fname))
        return jsonify({"saved": cfg.name, "file": fname}), 201

    @app.get("/api/compcfg")
    def get_compcfg():
        """全局计算配置 (唯一一份, 前端计算表单初始值来源)."""
        out = {k: settings[k] for k in COMPUTE_FIELDS}
        out["path"] = os.path.abspath(CONFIG_FILE)
        return jsonify(out)

    @app.post("/api/compcfg")
    def set_compcfg():
        """保存全局计算配置."""
        body = request.get_json(force=True, silent=True) or {}
        try:
            settings.update(body)
            settings.save_file()
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"saved": True, "path": os.path.abspath(CONFIG_FILE)}), 200

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
        # 全局计算配置: 每次提交后自动更新 (重启后恢复上次提交的计算设置)
        try:
            eff = dict(cfg)
            if jobs_override and jobs_override > 0:
                eff["jobs"] = int(jobs_override)
            settings.update(eff)
            settings.save_file()
        except Exception:
            pass  # 配置保存失败不影响任务提交
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