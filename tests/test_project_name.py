from __future__ import annotations

import json

import pytest

from orbcalc import DEFAULT_PROJECT_NAME
from orbcalc.config import TrajConfig, auto_project_name, ensure_project_name


def test_default_name_is_blank_and_generated_name_uses_sequence_and_window_start_months():
    seq = ["EARTH", "VENUS", "VENUS", "EARTH", "JUPITER", "SATURN"]
    eras = [["1997-10-01", "1997-10-31"], ["2029-01-01", "2033-06-30"]]

    assert DEFAULT_PROJECT_NAME == ""
    assert auto_project_name(seq, eras) == "EVVEJS 1997-10+2029-01"


def test_route_codes_use_single_m_for_mercury_and_mars_and_empty_eras():
    assert auto_project_name(["MERCURY", "MARS"], []) == "MM"


def test_ensure_project_name_generates_only_when_name_is_blank():
    cfg = TrajConfig()
    assert cfg.name == ""
    assert ensure_project_name(cfg) == "EVVEJS 1997-01"

    cfg.name = "  My mission  "
    assert ensure_project_name(cfg) == "My mission"


@pytest.mark.requires_pykep
def test_job_submission_persists_generated_name_and_preserves_explicit_names(monkeypatch, tmp_path):
    from webapp.app import JobManager

    monkeypatch.setattr(JobManager, "_supervise", lambda self: None)
    manager = JobManager(tmp_path, config={})
    monkeypatch.setattr(manager, "_start", lambda jid: None)

    cfg = dict(TrajConfig())
    generated_id = manager.submit(cfg, jobs_override=1)
    generated_job = manager._jobs[generated_id]
    effective = json.loads((tmp_path / generated_id / "config.json").read_text(encoding="utf-8"))
    request = json.loads((tmp_path / generated_id / "request.json").read_text(encoding="utf-8"))
    assert generated_job["name"] == "EVVEJS 1997-01"
    assert effective["name"] == generated_job["name"]
    assert request["name"] == generated_job["name"]

    cfg["name"] = "Manual name"
    explicit_id = manager.submit(cfg, jobs_override=1)
    assert manager._jobs[explicit_id]["name"] == "Manual name"
