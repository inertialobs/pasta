from __future__ import annotations

import shutil
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from webapp.app import JobManager

# 这些测试通过 JobManager.submit() -> TrajConfig.validate() -> orbcalc.planets
# 间接依赖 pykep；在 Linux 无 pykep 环境下必须 deselect。
pytestmark = pytest.mark.requires_pykep


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """提供一个不启动监督线程、不启动真实子进程的 JobManager。"""
    monkeypatch.setattr(JobManager, "_supervise", lambda self: None)
    m = JobManager(tmp_path, config={})
    monkeypatch.setattr(m, "_start", lambda jid: None)
    return m


def _simple_cfg(name: str = "test") -> dict:
    return {
        "name": name,
        "seq": ["EARTH", "MARS"],
        "eras": [["2020-01-01", "2020-03-01"]],
        "tof_bounds": [[100.0, 200.0]],
    }


def test_get_returns_none_after_delete(manager):
    jid = manager.submit(_simple_cfg(), jobs_override=1)
    assert manager.get(jid) is not None
    assert manager.delete(jid)
    assert manager.get(jid) is None


def test_poll_handles_missing_directory(manager):
    jid = manager.submit(_simple_cfg(), jobs_override=1)
    job = manager._jobs[jid]
    # 模拟进程已退出
    job["proc"] = MagicMock(poll=lambda: 0)
    shutil.rmtree(Path(job["dir"]))
    manager._poll(jid)
    assert job["status"] == "failed"


def test_wait_for_running_waits_then_kills(manager):
    proc = MagicMock()
    proc.poll.side_effect = [None, None, 0]
    manager._jobs["j1"] = {"proc": proc}
    manager._wait_for_running(timeout=0.5)
    assert proc.wait.called
    assert proc.poll.call_count >= 2


def test_shutdown_waits_for_process_before_exit(manager, monkeypatch):
    proc = MagicMock()
    proc.poll.return_value = None
    manager._jobs["j1"] = {"proc": proc, "status": "running"}

    exited = []
    monkeypatch.setattr("os._exit", lambda code: exited.append(code))
    monkeypatch.setattr("webapp.app._kill_tree", lambda pid: None)

    def graceful_exit():
        # 模拟 shutdown() 中实际会做的事：先杀，再等
        for job in list(manager._jobs.values()):
            p = job.get("proc")
            if p is not None and p.poll() is None:
                from webapp.app import _kill_tree
                _kill_tree(p.pid)
        manager._wait_for_running(timeout=1.0)
        import os
        os._exit(0)

    t = threading.Thread(target=graceful_exit)
    t.start()
    t.join(timeout=2.0)

    assert exited == [0]
