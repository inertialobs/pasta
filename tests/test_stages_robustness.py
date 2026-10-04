from __future__ import annotations

import numpy as np
import pytest

from orbcalc.config import TrajConfig


class FakeUdp:
    def __init__(self):
        self.udp = object()


@pytest.fixture
def stages():
    pytest.importorskip("pykep")
    from orbcalc import stages
    return stages


@pytest.mark.requires_pykep
def test_candidate_key_returns_key_and_info(stages, monkeypatch):
    cfg = TrajConfig()
    fake_info = {"tofs": [100.0, 200.0], "dsm_total": 150.0}
    monkeypatch.setattr(stages, "decode", lambda x, udp: fake_info)
    key, info = stages.candidate_key(cfg, np.zeros(10), FakeUdp())
    assert info is fake_info
    assert key[0] == 0  # feasible


@pytest.mark.requires_pykep
def test_candidate_key_returns_worst_on_decode_failure(stages, monkeypatch):
    cfg = TrajConfig()

    def _boom(x, udp):
        raise RuntimeError("boom")

    monkeypatch.setattr(stages, "decode", _boom)
    key, info = stages.candidate_key(cfg, np.zeros(10), FakeUdp())
    assert info is None
    assert key == (1, 1e18, 1e18)


@pytest.mark.requires_pykep
def test_pick_best_skips_failed_candidates(stages, monkeypatch):
    cfg = TrajConfig()
    good_info = {"tofs": [100.0, 200.0], "dsm_total": 150.0}

    def _decode(x, udp):
        if x[0] == 0:
            raise RuntimeError("bad")
        return good_info

    monkeypatch.setattr(stages, "decode", _decode)
    candidates = [(np.array([0.0] * 10), FakeUdp()), (np.array([1.0] * 10), FakeUdp())]
    best = stages.pick_best(cfg, candidates)
    assert best[0] is good_info
    assert best[1][0] == 1.0


@pytest.mark.requires_pykep
def test_pick_best_raises_when_all_candidates_fail(stages, monkeypatch):
    cfg = TrajConfig()

    def _boom(x, udp):
        raise RuntimeError("boom")

    monkeypatch.setattr(stages, "decode", _boom)
    candidates = [(np.zeros(10), FakeUdp()), (np.ones(10), FakeUdp())]
    with pytest.raises(RuntimeError, match="all candidates failed to decode"):
        stages.pick_best(cfg, candidates)
