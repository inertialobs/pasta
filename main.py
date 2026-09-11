# -*- coding: utf-8 -*-
    #Copyright (C) 2026  Inertial

    #This program is free software: you can redistribute it and/or modify
    # it under the terms of the GNU General Public License as published by
    # the Free Software Foundation, either version 3 of the License, or
    # (at your option) any later version.

    # This program is distributed in the hope that it will be useful,
    # but WITHOUT ANY WARRANTY; without even the implied warranty of
    # MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    # GNU General Public License for more details.

    # You should have received a copy of the GNU General Public License
    # along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

    python main.py [--host 127.0.0.1] [--port 8765] [--jobs 8] [--no-browser]

系统配置:
    启动前先读取 pasta.settings.json (工作目录下), 命令行参数优先于文件;
    文件内可设 host=0.0.0.0 (局域网, 附安全警告) 等。

端口策略:
    --port 0  -> 随机选空闲端口
    其他      -> 固定该端口; 被占用时视为已有实例, 直接打开其前端并退出
"""
from _version import __version__

import bootstrap

import argparse
import io
import multiprocessing
import os
import platform
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from orbcalc import slog
from settings import settings


def find_free_port(start: int, tries: int = 11) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"端口 {start}..{start + tries - 1} 全部被占用")


def main():
    os.chdir(Path(sys.executable).resolve().parent
         if getattr(sys, "frozen", False)
         else Path(__file__).resolve().parent)
    if len(sys.argv) >= 2 and sys.argv[1] == "--cli":
        # 当以子进程模式启动的时候， 剥去--cli并传入run_cli.main()
        sys.argv = [sys.argv[0]] + (sys.argv[2:] if len(sys.argv) > 2 else [])
        from orbcalc.run_cli import main as cli_main
        sys.exit(cli_main())

    ap = argparse.ArgumentParser(description=f"PASTA Web Launcher {__version__}")
    ap.add_argument("--host", default=None, help="监听地址 (默认取系统配置 127.0.0.1; 0.0.0.0=局域网, 仅限安全内网)")
    ap.add_argument("--port", type=int, default=None, help="端口 (默认取系统配置 8765; 0=随机空闲端口)")
    ap.add_argument("--jobs", type=int, default=None, help="默认并行进程数 (可被任务配置覆盖; 缺省用配置值)")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    # 统一日志 (写 console, UTF-8)
    for _s in (sys.stdout, sys.stderr):
        try:
            if isinstance(_s, io.TextIOWrapper):
                _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    slog.setup(stream=sys.stdout, debug=False)
    slog.inf(f"[env] PASTA web | ver={__version__} | pid={os.getpid()} | "
             f"py={platform.python_version()} | os={platform.platform()} | cpu={os.cpu_count()}")

    # 配置 (文件) -> 命令行覆盖
    try:
        settings.load_file()
    except Exception as e:
        slog.wrn(f"[settings] 读取失败, 使用默认设置: {e}")
    if args.host:
        settings.update({"host": args.host})
    if args.port is not None:
        settings.update({"port": args.port})
    settings.validate()

    host, port = settings["host"], settings["port"]
    is_lan = host in ("0.0.0.0", "::")
    open_browser = not args.no_browser and bool(settings["open_browser"])
    slog.inf(f"[net] host={host} port={port} is_lan={is_lan} browser={open_browser}")

    # 0.0.0.0 安全警告
    if is_lan:
        slog.wrn("监听 0.0.0.0: 局域网内任何设备可访问, 且本工具无鉴权 — 仅限安全内网使用!")

    import pykep
    import pygmo
    slog.inf(f"[env] pykep {pykep.__version__}  pygmo {pygmo.__version__}")

    from webapp.app import app
    if args.jobs:
        app.config["DEFAULT_JOBS"] = args.jobs

    # ---- 端口策略 ----
    if port == 0:
        port = find_free_port(8765)
        slog.inf(f"[main] 随机端口: {port}")
    else:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, port))
        except OSError:
            # 端口已被占用 = 已有实例在运行: 直接打开其前端并退出
            url = f"http://127.0.0.1:{port}/"
            slog.inf(f"[main] 端口 {port} 已被实例占用, 打开已有实例: {url}")
            webbrowser.open(url)
            sys.exit(0)

    url = f"http://127.0.0.1:{port}/"
    slog.inf(f"[main] PASTA Web: {url}")

    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port,
                               threaded=True, use_reloader=False),
        daemon=True,
    )
    thread.start()

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        slog.inf("[main] stopped")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
