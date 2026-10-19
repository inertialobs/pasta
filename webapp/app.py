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
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

from orbcalc import COMPUTE_FIELDS, SYS_FIELDS, TRAJ_FIELDS, ephem, horizons, planets, slog
from orbcalc.config import TrajConfig, ensure_project_name, sanitize_name
from orbcalc.settings import CONFIG_FILE, settings, resolve_compute

from _version import (__version__, commit, commit_full, commit_date,
                      commit_subject, dirty, build_time, describe)

# 运行根目录: main.py 启动时已 chdir 到此 (源码=项目根, 冻结=exe 目录)。
# 资源 (webapp/presets) 与用户数据 (runs/presets) 同根。
ROOT = Path.cwd()
RUNS_DIR = ROOT / "runs"
PRESETS_DIR = ROOT / "presets"          # 用户预设 (可写)
EPHEMERIS_DIR = ROOT / ephem.DIRNAME    # 外部星历 (*.bsp, 用户自备)
for _d in (RUNS_DIR, PRESETS_DIR, EPHEMERIS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# 启用系统配置里的外部星历 (启用列表只影响可用天体/Kernel 加载)
planets.set_active(settings["ephemerides"])


def _job_dir(jid: str) -> Path | None:
    """jid -> 合法的 runs/<jid>; 含分隔符/穿越/逃逸时返回 None。"""
    base = RUNS_DIR.resolve()
    p = (base / jid).resolve()
    return p if p.parent == base else None


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _kill_tree(pid: int) -> None:
    """整棵进程树终止 (含 multiprocessing 池 workers 等子进程).
    Windows 用 taskkill /T /F; POSIX 先 SIGTERM, 短暂等待后若仍存活则 SIGKILL。
    (子进程以 start_new_session=True 启动, 故其 pgid == pid)。"""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        except Exception:
            pass
        return
    # POSIX: 进程组 SIGTERM, 等待后若仍存活则 SIGKILL
    try:
        os.killpg(pid, signal.SIGTERM)
    except Exception:
        pass
    for _ in range(20):
        try:
            os.killpg(pid, 0)          # 探测进程组是否仍存在
        except ProcessLookupError:
            return
        except Exception:
            break
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


class JobManager:
    """任务生命周期管理: submit / poll / cancel / queue / artifacts."""

    MAX_RUNNING = 1

    def __init__(self, runs_dir: Path, config=None):
        self.runs_dir = Path(runs_dir)
        self.config = config if config is not None else {}   # 共享 Flask app.config
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
        cfg.validate()
        ensure_project_name(cfg)
        comp = resolve_compute(config_dict, jobs_override)
        job_cfg = {**dict(cfg), **comp}   # 轨迹 + 计算快照, 供 run_cli 读取
        job_cfg["ephemerides"] = list(settings["ephemerides"])   # 外部星历 (子进程/worker 加载)
        request_cfg = dict(config_dict)
        request_cfg["name"] = cfg.name
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
            with open(d / "config.json", "w", encoding="utf-8") as f:
                json.dump(job_cfg, f, ensure_ascii=False, indent=2)
            with open(d / "request.json", "w", encoding="utf-8") as f:
                json.dump(request_cfg, f, ensure_ascii=False, indent=2)
            self._jobs[jid] = {
                "job_id": jid,
                "name": cfg.name,
                "created": time.time(),
                "status": "queued" if self._active else "starting",
                "jobs": comp["jobs"],
                "dir": str(d),
            }
            if self._active is None:
                self._start(jid)
            else:
                self._queue.append(jid)
        slog.inf(f"[job] submit jid={jid} name={cfg.name} jobs={comp['jobs']} seq={cfg.seq}")
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
        extra = ["--debug"] if self.config.get("DEBUG_LOG") else []
        if getattr(sys, "frozen", False):
            # 冻结版无 python 解释器: 子进程 = 当前 exe 的 --cli 模式
            cmd = [sys.executable, "--cli",
                   "--config", str(d / "config.json"),
                   "--jobs", str(job["jobs"]),
                   "--outdir", str(d)] + extra
        else:
            cmd = [sys.executable, "-m", "orbcalc.run_cli",
                   "--config", str(d / "config.json"),
                   "--jobs", str(job["jobs"]),
                   "--outdir", str(d)] + extra
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
        # 终结状态只处理一次: 加锁占位 finished (请求线程与监督线程都会进来)
        with self._lock:
            if job.get("finished") is not None:
                return
            job["finished"] = time.time()
            if self._active == jid:
                self._active = None
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
        try:
            if result_path.exists():
                # result.json 是权威: run_cli 正常收尾总会写 (ok/no_candidates/
                # cancelled/error), 口径与 _disk_status / 历史任务一致。
                # 注: 已知 pykep/OpenBLAS 在部分父进程上下文下于解释器退出期崩溃
                # (rc=-6, "corrupted double-linked list"), 此时其实已写完结果;
                # 故此处以结果文件为准, 不再依赖 rc。退出期崩溃本身尚未处理。
                job["status"] = self._disk_status(Path(job["dir"]))
            elif job.get("cancelled"):
                # 无结果时才看 cancel 标志: taskkill /F 退出码非 130, 不能只看 rc
                job["status"] = "cancelled"
            elif rc == 130:
                job["status"] = "cancelled"
            else:
                job["status"] = "failed"
        except OSError:
            job["status"] = "failed"
        job["rc"] = rc
        slog.inf(f"[job] end jid={jid} status={job['status']} rc={rc} "
                 f"elapsed={job['finished'] - job.get('started', job['finished']):.1f}s")

    # ------------------------------------------------------------------
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
        try:
            if rf.exists():
                cfg = json.loads(rf.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            cfg = {}
        # request.json 内容就是扁平 config (submit 时 json.dump(config_dict))
        try:
            all_lines = (d / "log.txt").read_text(
                encoding="utf-8", errors="replace").splitlines()
        except (OSError, ValueError):
            all_lines = []
        try:
            has_result = (d / "result.json").exists()
        except OSError:
            has_result = False
        return {
            "job_id": jid,
            "name": cfg.get("name", jid),
            "created": self._dir_mtime(d),
            "status": self._disk_status(d),
            "jobs": cfg.get("jobs"),
            "dir": str(d),
            "elapsed_s": None,
            "has_result": has_result,
            "log_tail": all_lines[-250:],
            "log_len": len(all_lines),
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
        # 删除可能随时发生，读文件前必须容忍目录消失
        if not d.is_dir():
            return None

        result_path = d / "result.json"
        try:
            out["has_result"] = result_path.exists()
        except OSError:
            out["has_result"] = False

        log_path = d / "log.txt"
        try:
            if log_path.exists():
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                out["log_tail"] = lines[-250:]
                out["log_len"] = len(lines)
            else:
                out["log_tail"] = []
                out["log_len"] = 0
        except OSError:
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

    def _wait_for_running(self, timeout: float = 10.0) -> None:
        """等待所有运行中子进程退出；超时后再次强制杀。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            alive = []
            with self._lock:
                for job in self._jobs.values():
                    proc = job.get("proc")
                    if proc is not None and proc.poll() is None:
                        alive.append(proc)
            if not alive:
                return
            for proc in alive:
                try:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    proc.wait(timeout=remaining)
                except Exception:
                    pass
        # 超时：再次强制杀仍存活的进程
        with self._lock:
            for job in self._jobs.values():
                proc = job.get("proc")
                if proc is not None and proc.poll() is None:
                    try:
                        _kill_tree(proc.pid)
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


# 字段分组 (TRAJ_FIELDS / SYS_FIELDS / COMPUTE_FIELDS) 见 orbcalc/__init__.py


def _subdict(d: dict, fields: set) -> dict:
    return {k: v for k, v in d.items() if k in fields}


def _short_body_name(label: str) -> str:
    """SBDB 显示名去掉前导编号: '162173 Ryugu' -> 'Ryugu'。"""
    s = re.sub(r"^\s*\d+\s+", "", str(label or "")).strip()
    return s or str(label or "")


def _slug(text: str, fallback: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or fallback


def _sidecar_naif(p: Path):
    try:
        return int(json.loads(p.with_suffix(".json").read_text(encoding="utf-8")).get("naif"))
    except Exception:
        return None


# 外部星历下载任务 (后台线程 + 进度轮询; 下载可能持续数十秒到数分钟)
_EPH_TASKS: dict = {}
_EPH_LOCK = threading.Lock()


def _eph_update(tid, **kw):
    with _EPH_LOCK:
        t = _EPH_TASKS.get(tid)
        if t is not None:
            t.update(kw)


def _ephemeris_add_worker(tid, body):
    """后台: 解析 -> Horizons 取数(带进度) -> 写 SPK+旁挂 -> 启用。"""
    try:
        naif, cmd = body.get("naif"), body.get("horizons")
        label = str(body.get("name") or body.get("label") or "")
        if naif is None or not cmd:
            cand = horizons.resolve(str(body.get("query") or ""))
            if not cand or cand.get("naif") is None or not cand.get("horizons"):
                return _eph_update(tid, status="error", error="无法解析该天体 (未编号小天体暂不支持)")
            naif, cmd, label = cand["naif"], cand["horizons"], cand["label"]
        naif = int(naif)
        d0, d1 = horizons.default_span()
        t0 = float(body.get("t0", d0)); t1 = float(body.get("t1", d1))
        if t1 <= t0:
            return _eph_update(tid, status="error", error="结束时间需晚于开始时间")
        span = t1 - t0
        step = float(body.get("step") or 0) or max(3.0, min(5.0, span / 6000.0))
        prev = ephem.file_for_naif(naif)             # 与已有 kernel 取并集 (不丢旧范围)
        if prev and prev.get("coverage"):
            t0, t1 = min(t0, float(prev["coverage"][0])), max(t1, float(prev["coverage"][1]))
        _eph_update(tid, mode="fetch")
        states, ets, meta = horizons.fetch(cmd, t0, t1, step,
                                           progress=lambda d, n: _eph_update(tid, done=d, total=n))
        if len(ets) < 8:
            return _eph_update(tid, status="error", error=f"取到的采样点太少 ({len(ets)})")
        short = _short_body_name(label) or f"NAIF {naif}"
        slug = _slug(short, f"naif_{naif}")
        path = EPHEMERIS_DIR / f"{slug}.bsp"
        if path.exists() and _sidecar_naif(path) not in (None, naif):
            slug, path = f"{slug}_{naif}", EPHEMERIS_DIR / f"{slug}_{naif}.bsp"
        _eph_update(tid, mode="write")
        horizons.write_spk(path, naif, slug.upper(), states, ets)
        radius = meta.get("radius")
        mu = meta.get("mu_self")
        cov = [(ets[0] + horizons.ET0) / 86400.0, (ets[-1] + horizons.ET0) / 86400.0]
        side = {"naif": naif, "name": short, "mu_self": mu, "radius": radius,
                "safe_radius": (1.05 * radius if radius else 0.0),
                "coverage": cov, "horizons": cmd}
        try:
            (EPHEMERIS_DIR / f"{slug}.json").write_text(
                json.dumps(side, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        ephem.reset_cache()
        files = list(settings["ephemerides"])
        if path.name not in files:
            files.append(path.name)
        settings.update({"ephemerides": files})
        settings.save_file()
        planets.set_active(files)
        warn = "远端数据未完全覆盖请求跨度" if (cov[0] > t0 + 0.5 or cov[1] < t1 - 0.5) else None
        info = ephem.file_for_naif(naif) or {}
        _eph_update(tid, status="done", result={
            "file": path.name, "naif": naif, "name": short, "coverage": cov,
            "gm_m3s2": mu, "radius_m": radius,
            "bodies": [b["name"] for b in info.get("bodies", [])], "warning": warn})
    except Exception as e:
        _eph_update(tid, status="error", error=f"{type(e).__name__}: {e}")


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
        traj[title] = _subdict(dict(cfg), TRAJ_FIELDS)


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
    jm = JobManager(RUNS_DIR, app.config)

    # ------------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/logo.png")
    def logo():
        return send_from_directory(ROOT / "assets", "logo.png")

    @app.get("/api/health")
    def health():
        import pykep
        host = settings["host"]
        return jsonify({"ok": True, "version": __version__, "describe": describe(),
                        "commit": commit, "dirty": dirty, "build_time": build_time,
                        "pykep": pykep.__version__,
                        "runs_dir": str(RUNS_DIR),
                        "host": host, "port": settings["port"],
                        "lan": host in ("0.0.0.0", "::")})

    @app.get("/api/about")
    def about():
        """关于页数据: 应用/构建/运行环境 + 许可全文 (LICENSE / ThirdPartyNotice.md)。"""
        import platform
        import sys
        import pykep
        try:
            import pygmo
            pygmo_ver = pygmo.__version__
        except Exception:
            pygmo_ver = None
        try:
            import numpy
            numpy_ver = numpy.__version__
        except Exception:
            numpy_ver = None

        def _read(name):
            try:
                return (ROOT / name).read_text(encoding="utf-8")
            except Exception:
                return None

        return jsonify({
            "name": "PASTA",
            "full_name": "Parallel Astrodynamic Solver for Trajectory Analysis",
            "version": __version__,
            "describe": describe(),
            "commit": commit,
            "commit_full": commit_full,
            "commit_date": commit_date,
            "commit_subject": commit_subject,
            "dirty": dirty,
            "build_time": build_time,
            "license": "GPL-3.0-or-later",
            "copyright": "Copyright (C) 2026 Inertial",
            "repo": "https://github.com/inertialobs/pasta",
            "releases": "https://github.com/inertialobs/pasta/releases",
            "runtime": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "frozen": bool(getattr(sys, "frozen", False)),
                "pykep": pykep.__version__,
                "pygmo": pygmo_ver,
                "numpy": numpy_ver,
            },
            "license_text": _read("LICENSE"),
            "thirdparty_text": _read("ThirdPartyNotice.md"),
        })

    @app.get("/api/sysconfig")
    def get_sysconfig():
        out = {k: settings[k] for k in SYS_FIELDS}
        out["is_lan"] = settings["host"] in ("0.0.0.0", "::")
        out["path"] = os.path.abspath(CONFIG_FILE)
        out["ephemeris_options"] = ephem.options()
        out["ephemeris_span"] = list(horizons.default_span())
        return jsonify(out)

    @app.post("/api/sysconfig")
    def set_sysconfig():
        """保存系统配置 (host/port/浏览器/星历…)."""
        body = request.get_json(force=True, silent=True) or {}
        try:
            settings.update(body)
            settings.save_file()
            if "ephemerides" in body:
                ephem.reset_cache()
                planets.set_active(settings["ephemerides"])
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"saved": True, "path": os.path.abspath(CONFIG_FILE),
                        "restart_required": True}), 200

    @app.post("/api/ephemeris/rescan")
    def rescan_ephemeris():
        """重新扫描 ephemeris/ 并套用当前启用列表."""
        ephem.reset_cache()
        planets.set_active(settings["ephemerides"])
        return jsonify({"options": ephem.options()})

    @app.get("/api/bodies")
    def list_bodies():
        """可用天体: 内置行星 ∪ 已启用 kernel 天体 (供前端下拉)."""
        out = {}
        for tag in sorted(planets.known_tags()):
            info = planets.body_info(tag)
            if info:
                out[tag] = info
        return jsonify(out)

    @app.get("/api/horizons/search")
    def horizons_search():
        """检索天体 (SBDB 小天体 + NAIF 名/id)。"""
        try:
            return jsonify({"results": horizons.search(request.args.get("q", ""))})
        except Exception as e:
            return jsonify({"error": f"检索失败: {e}"}), 502

    @app.post("/api/ephemeris/add")
    def ephemeris_add():
        """启动后台下载任务 (Horizons 抓取可能较久), 返回 task_id 供轮询进度。"""
        body = request.get_json(force=True, silent=True) or {}
        tid = uuid.uuid4().hex
        with _EPH_LOCK:
            _EPH_TASKS[tid] = {"status": "running", "mode": "start", "done": 0, "total": 0}
        threading.Thread(target=_ephemeris_add_worker, args=(tid, body), daemon=True).start()
        return jsonify({"task_id": tid}), 202

    @app.get("/api/ephemeris/task/<tid>")
    def ephemeris_task(tid):
        with _EPH_LOCK:
            t = _EPH_TASKS.get(tid)
            if t is not None and t.get("status") in ("done", "error"):
                _EPH_TASKS.pop(tid, None)     # 终态取走后清理
        if t is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(t)

    @app.delete("/api/ephemeris/<file>")
    def ephemeris_delete(file):
        """删除一个 kernel (+旁挂) 并从启用列表移除。"""
        if not file or "/" in file or "\\" in file or file in (".", ".."):
            return jsonify({"error": "bad name"}), 400
        f = EPHEMERIS_DIR / file
        if not f.is_file():
            return jsonify({"error": "not found"}), 404
        try:
            ephem.unload([file])                      # 释放该 kernel 的 SPICE 文件锁
            ephem.reset_cache()
            planets.set_active(settings["ephemerides"])
            f.unlink()
            (EPHEMERIS_DIR / (f.stem + ".json")).unlink(missing_ok=True)
        except OSError as e:
            return jsonify({"error": str(e)}), 500
        files = [x for x in settings["ephemerides"] if x != file]
        settings.update({"ephemerides": files})
        settings.save_file()
        ephem.reset_cache()
        planets.set_active(files)
        return jsonify({"deleted": file})

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
            settings.update(resolve_compute(cfg, jobs_override))
            settings.save_file()
        except Exception:
            pass  # 配置保存失败不影响任务提交
        return jsonify({"job_id": jid, "status": jm.get(jid)["status"]}), 201

    @app.get("/api/jobs")
    def list_jobs():
        return jsonify(jm.list())

    @app.get("/api/jobs/<jid>")
    def job_status(jid):
        if _job_dir(jid) is None:
            return jsonify({"error": "not found"}), 404
        info = jm.get(jid)
        if info is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(info)

    @app.post("/api/jobs/<jid>/cancel")
    def cancel_job(jid):
        if _job_dir(jid) is None:
            return jsonify({"error": "not found"}), 404
        ok = jm.cancel(jid)
        if not ok:
            return jsonify({"error": "not found"}), 404
        return jsonify({"job_id": jid, "cancelled": True})

    @app.delete("/api/jobs/<jid>")
    def delete_job(jid):
        if _job_dir(jid) is None:
            return jsonify({"error": "not found"}), 404
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
        d = _job_dir(jid)
        if d is None:
            return jsonify({"error": "not found"}), 404
        f = d / artifact
        if not f.exists():
            return jsonify({"error": "artifact not ready"}), 404
        return send_from_directory(d, artifact, as_attachment=False)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8765, threaded=True, debug=False)


