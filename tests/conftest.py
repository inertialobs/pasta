from __future__ import annotations

import copy
import importlib.util
import json
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def flask_app():
    # The web module does not import pykep until a trajectory API is called.
    from webapp.app import app

    return app


@pytest.fixture(scope="session")
def live_server(flask_app):
    server = make_server("127.0.0.1", 0, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def editor_page(page, live_server):
    """Run the real editor JS while mocking only its HTTP APIs.

    This isolates sequence/era editing from pykep, subprocess jobs and the
    optimizer, so the browser tests assert UI state rather than trajectories.
    """
    from orbcalc import COMPUTE_DEFAULTS, TRAJ_DEFAULTS

    raw = json.loads((ROOT / "presets" / "traj_evvejs_cassini.json").read_text(encoding="utf-8"))
    preset = copy.deepcopy(TRAJ_DEFAULTS)
    preset.update(raw)
    title = raw["title"]
    comp = {**COMPUTE_DEFAULTS, "jobs": 1, "era_step_d": 10}
    api = {
        "/api/health": {"host": "127.0.0.1", "port": 8765, "describe": "test", "lan": False},
        "/api/presets": {title: preset},
        "/api/compcfg": comp,
        "/api/jobs": [],
    }

    def mock_api(route):
        payload = api.get(route.request.url.split("?", 1)[0].removeprefix(live_server))
        if payload is None:
            route.fulfill(status=404, body="unexpected API request")
            return
        route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            body=json.dumps(payload, ensure_ascii=False),
        )

    page.route("**/api/**", mock_api)
    page.goto(live_server)
    page.wait_for_function(
        "() => { try { const c = JSON.parse(document.querySelector('#cfgJsonBox').value); "
        "return c.name === 'EVVEJS Cassini 1997-10' && c.jobs === 1 && c.era_step_d === 10; "
        "} catch { return false; } }"
    )
    return page


def pytest_collection_modifyitems(items):
    if importlib.util.find_spec("pykep") is not None:
        return
    skip = pytest.mark.skip(reason="requires the native pykep package")
    for item in items:
        if "requires_pykep" in item.keywords:
            item.add_marker(skip)
