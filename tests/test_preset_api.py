from __future__ import annotations

import copy
import json

import pytest

pytestmark = pytest.mark.requires_pykep


@pytest.fixture
def api_client_and_preset_dir(monkeypatch, tmp_path):
    import webapp.app as web_module

    monkeypatch.setattr(web_module, "PRESETS_DIR", tmp_path)
    return web_module.app.test_client(), tmp_path


def _ordered_config():
    from orbcalc import TRAJ_DEFAULTS

    config = copy.deepcopy(TRAJ_DEFAULTS)
    config.update(
        {
            "seq": ["EARTH", "VENUS", "MARS", "SATURN"],
            "eras": [["1997-01-01", "1997-03-01"], ["2001-02-01", "2001-04-01"]],
            "tof_bounds": [[170, 220], [300, 500], [900, 1400]],
        }
    )
    return config


def test_preset_api_roundtrip_preserves_node_window_and_leg_order(api_client_and_preset_dir):
    client, _ = api_client_and_preset_dir
    config = _ordered_config()

    response = client.post("/api/presets", json={"name": "Order roundtrip", "config": config})
    assert response.status_code == 201

    presets = client.get("/api/presets").get_json()
    saved = presets["Order roundtrip"]
    assert saved["seq"] == config["seq"]
    assert saved["eras"] == config["eras"]
    assert saved["tof_bounds"] == config["tof_bounds"]
    assert len(saved["tof_bounds"]) == len(saved["seq"]) - 1


def test_preset_api_rejects_mismatched_leg_count_without_saving(api_client_and_preset_dir):
    client, preset_dir = api_client_and_preset_dir
    config = _ordered_config()
    config["tof_bounds"].pop()

    response = client.post("/api/presets", json={"name": "Invalid order", "config": config})

    assert response.status_code == 400
    assert list(preset_dir.glob("*.json")) == []
