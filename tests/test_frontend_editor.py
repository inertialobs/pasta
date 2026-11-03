from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from playwright.sync_api import expect

ROOT = Path(__file__).resolve().parents[1]


def read_config(page):
    return json.loads(page.locator("#cfgJsonBox").input_value())


def assert_roles_and_leg_labels(page, seq):
    nodes = page.locator("#seqNodes .node-row")
    assert nodes.count() == len(seq)
    classes = [nodes.nth(i).get_attribute("class") for i in range(len(seq))]
    expected_roles = ["depart"] + ["flyby"] * (len(seq) - 2) + ["arrive"]
    assert classes == [f"node-row {r}" for r in expected_roles]

    rows = page.locator("#tofTable table tr")
    assert rows.count() == len(seq)  # header + one row per adjacent-node leg
    labels = [rows.nth(i + 1).locator("td").nth(0).inner_text().strip()
              for i in range(len(seq) - 1)]
    assert labels == [f"{seq[i]} → {seq[i + 1]}" for i in range(len(seq) - 1)]
    assert page.locator("#tofTable .leg-lam").count() == len(seq) - 1


def _open_ephemeris(page):
    page.locator("#sysOpen").click()
    page.wait_for_selector("#sysModal:not(.hidden)")
    page.locator('#sysModal .side-item[data-pane="ephemeris"]').click()


def _mock_presets_page(page, live_server, presets):
    """Serve the mocked API (with the given presets) and navigate to the app."""
    from orbcalc import COMPUTE_DEFAULTS

    api = {
        "/api/health": {"host": "127.0.0.1", "port": 8765, "describe": "test", "lan": False},
        "/api/presets": presets,
        "/api/compcfg": {**COMPUTE_DEFAULTS, "jobs": 1, "era_step_d": 10},
        "/api/jobs": [],
    }

    def handler(route):
        payload = api.get(route.request.url.split("?", 1)[0].removeprefix(live_server))
        if payload is None:
            route.fulfill(status=404, body="unexpected API request")
            return
        route.fulfill(status=200, content_type="application/json; charset=utf-8",
                      body=json.dumps(payload, ensure_ascii=False))

    page.route("**/api/**", handler)
    page.goto(live_server)


def _evvejs_default():
    import copy

    from orbcalc import TRAJ_DEFAULTS

    raw = json.loads((ROOT / "presets" / "traj_evvejs_cassini.json").read_text(encoding="utf-8"))
    cfg = copy.deepcopy(TRAJ_DEFAULTS)
    cfg.update(raw)
    return raw["title"], cfg


def leg_lam(page, leg):
    return page.locator(f'#tofTable .leg-lam[data-leg="{leg}"]')


def test_editor_state_matches_backend(editor_page):
    page = editor_page
    assert page.locator("#cfgName").input_value() == ""
    original = read_config(page)
    original_seq = original["seq"]
    original_bounds = original["tof_bounds"]
    original_eras = original["eras"]

    # 发射窗口: 追加、保持顺序、至少保留一个
    page.locator("#eraAdd").click()
    after_one = read_config(page)["eras"]
    assert after_one[0] == original_eras[0]
    assert after_one[1][0] == original_eras[-1][1]
    assert date.fromisoformat(after_one[1][1]).year - date.fromisoformat(after_one[1][0]).year == 4

    page.locator("#eraAdd").click()
    after_two = read_config(page)["eras"]
    assert after_two[:2] == after_one
    assert after_two[2][0] == after_two[1][1]

    page.locator("#eraTable .era-row").nth(0).locator(".era-del").click()
    assert read_config(page)["eras"] == after_two[1:]
    remaining = page.locator("#eraTable .era-row")
    remaining.nth(0).locator(".era-del").click()
    assert read_config(page)["eras"] == [after_two[2]]
    expect(page.locator("#eraTable .era-del")).to_be_disabled()

    # 节点: 追加、改标签、移动、删除后序列与 TOF 腿保持对齐
    page.locator("#nodeAdd").click()
    added = read_config(page)
    expected = original_seq + ["VENUS"]
    assert added["seq"] == expected
    assert len(added["tof_bounds"]) == len(expected) - 1
    assert added["tof_bounds"][:len(original_bounds)] == original_bounds
    assert_roles_and_leg_labels(page, expected)

    last_index = len(expected) - 1
    tag_input = page.locator("#seqNodes .node-row").nth(last_index).locator('input[data-k="tag"]')
    tag_input.fill("MARS")
    tag_input.press("Enter")
    expected[last_index] = "MARS"
    assert read_config(page)["seq"] == expected
    assert_roles_and_leg_labels(page, expected)

    flyby_index = len(expected) - 2
    moved = page.locator("#seqNodes .node-row").nth(flyby_index)
    moved.locator(".node-move.up").click()
    expected[flyby_index - 1], expected[flyby_index] = expected[flyby_index], expected[flyby_index - 1]
    assert read_config(page)["seq"] == expected
    assert_roles_and_leg_labels(page, expected)

    moved = page.locator("#seqNodes .node-row").nth(flyby_index - 1)
    moved.locator(".node-move.dn").click()
    expected[flyby_index - 1], expected[flyby_index] = expected[flyby_index], expected[flyby_index - 1]
    assert read_config(page)["seq"] == expected
    assert_roles_and_leg_labels(page, expected)

    page.locator("#seqNodes .node-row").nth(last_index).locator(".node-del").click()
    final = read_config(page)
    assert final["seq"] == original_seq
    assert final["tof_bounds"] == original_bounds
    assert_roles_and_leg_labels(page, original_seq)

    # 出发/到达节点也可移动
    seq = read_config(page)["seq"]
    page.locator("#seqNodes .node-row").nth(0).locator(".node-move.dn").click()
    expected = [seq[1], seq[0]] + seq[2:]
    assert read_config(page)["seq"] == expected
    assert_roles_and_leg_labels(page, expected)

    last = len(expected) - 1
    page.locator("#seqNodes .node-row").nth(last).locator(".node-move.up").click()
    expected[last - 1], expected[last] = expected[last], expected[last - 1]
    assert read_config(page)["seq"] == expected
    assert_roles_and_leg_labels(page, expected)

    # UI 导出的配置必须能被后端接受
    from orbcalc.config import TrajConfig

    cfg = TrajConfig.from_dict(read_config(page))
    assert cfg.seq == expected
    assert len(cfg.tof_bounds) == len(cfg.seq) - 1
    assert len(cfg.lambert_types) == len(cfg.seq) - 1
    assert len(cfg.eras) == 1


def test_lambert_types_ui_roundtrip(editor_page):
    page = editor_page
    n_legs = len(read_config(page)["seq"]) - 1
    assert read_config(page)["lambert_types"] == [0] * n_legs

    btn = leg_lam(page, 0)
    expect(btn).to_have_text("1/2")
    btn.click()
    expect(btn).to_have_text("3")
    assert read_config(page)["lambert_types"] == [1] + [0] * (n_legs - 1)
    btn.click()
    expect(btn).to_have_text("4")
    assert read_config(page)["lambert_types"] == [2] + [0] * (n_legs - 1)
    btn.click()
    expect(btn).to_have_text("1/2")
    assert read_config(page)["lambert_types"] == [0] * n_legs

    # 按腿保留: 设置第 2 腿后增删节点不影响已有腿
    leg_lam(page, 1).click()
    assert read_config(page)["lambert_types"][1] == 1
    page.locator("#nodeAdd").click()
    assert read_config(page)["lambert_types"] == [0, 1] + [0] * (n_legs - 1)
    page.locator("#seqNodes .node-row").last.locator(".node-del").click()
    assert read_config(page)["lambert_types"] == [0, 1] + [0] * (n_legs - 2)

    # 载入混合类型配置后 UI 与后端一致
    page.evaluate("""() => fillTrajForm({
      name: "", seq: ["EARTH", "VENUS", "EARTH"],
      tof_bounds: [[300, 800], [300, 800]], lambert_types: [1, 2],
      eras: [["2029-01-01", "2030-06-30"]], objective: "min_dsm",
      objective_weights: [1, 0], penalty: [10, 0.2], frontier_penalty: [30, 2],
      dsm_limit_ms: 600, vinf_bounds_kmps: [3.5, 6], eta_bounds: [0.01, 0.9], rp_ub: 200 })""")
    expect(leg_lam(page, 0)).to_have_text("3")
    expect(leg_lam(page, 1)).to_have_text("4")
    final = read_config(page)
    assert final["lambert_types"] == [1, 2]

    from orbcalc.config import TrajConfig

    cfg = TrajConfig.from_dict(final)
    assert cfg.lambert_types == [1, 2]


def test_preset_selection(page, live_server):
    import copy

    from orbcalc import TRAJ_DEFAULTS

    title, default_cfg = _evvejs_default()
    other = copy.deepcopy(TRAJ_DEFAULTS)
    other.update({"seq": ["EARTH", "MARS"], "tof_bounds": [[100, 200]],
                  "eras": [["2029-01-01", "2030-01-01"]]})

    # 默认项故意排在后面 (dict 顺序即 API 返回顺序)
    _mock_presets_page(page, live_server, {"AAA other": other, title: default_cfg})
    page.wait_for_function(f"() => document.querySelector('#presetSelect').value === {title!r}")
    assert page.locator("#presetSelect").input_value() == title
    assert read_config(page)["seq"] == default_cfg["seq"]

    # 默认预设不存在 -> 回退第一项
    page.unroute_all()
    _mock_presets_page(page, live_server, {"AAA other": other})
    page.wait_for_function("() => document.querySelector('#presetSelect').value === 'AAA other'")
    assert read_config(page)["seq"] == ["EARTH", "MARS"]


def test_ephemeris_panel_and_body_picker(page, live_server):
    from orbcalc import COMPUTE_DEFAULTS

    title, default_cfg = _evvejs_default()
    bodies = {t: {"label": t.title(), "note": "", "builtin": True}
              for t in ["MERCURY", "VENUS", "EARTH", "MARS", "JUPITER",
                        "SATURN", "URANUS", "NEPTUNE"]}
    bodies["CERES"] = {"label": "Ceres", "note": "外部星历 (kernel)", "builtin": False}
    warning = "疑似损坏或不兼容: 1 个非法 NAIF id（如 1129403230）"
    api = {
        "/api/health": {"host": "127.0.0.1", "port": 8765, "describe": "test", "lan": False},
        "/api/presets": {title: default_cfg},
        "/api/compcfg": {**COMPUTE_DEFAULTS, "jobs": 1, "era_step_d": 10},
        "/api/jobs": [],
        "/api/bodies": bodies,
        "/api/sysconfig": {
            "host": "127.0.0.1", "port": 8765, "open_browser": True,
            "ephemerides": ["bad.bsp"],
            "ephemeris_options": [
                {"file": "bad.bsp", "size": 123, "n_bodies": 0,
                 "coverage": None, "warning": warning},
                {"file": "ok.bsp", "size": 456, "n_bodies": 14,
                 "coverage": None, "warning": None},
            ],
        },
    }

    def handler(route):
        payload = api.get(route.request.url.split("?", 1)[0].removeprefix(live_server))
        if payload is None:
            route.fulfill(status=404, body="unexpected API request")
            return
        route.fulfill(status=200, content_type="application/json; charset=utf-8",
                      body=json.dumps(payload, ensure_ascii=False))

    page.route("**/api/**", handler)
    page.goto(live_server)
    page.wait_for_selector("#ephWarnBanner:not(.hidden)")
    banner = page.locator("#ephWarnBanner").inner_text()
    assert "bad.bsp" in banner and "1129403230" in banner

    # kernel 天体进入行星下拉
    tag_input = page.locator("#seqNodes .node-row").first.locator('input[data-k="tag"]')
    tag_input.focus()
    page.wait_for_selector(".node-menu:not(.hidden) .opt[data-tag]")
    tags = page.locator(".node-menu:not(.hidden) .opt[data-tag]").evaluate_all(
        "els => els.map(e => e.dataset.tag)")
    assert "CERES" in tags

    _open_ephemeris(page)
    page.wait_for_selector("#sysEphemList .ephem-item")
    assert page.locator("#mpaneEphemeris").is_visible()
    assert page.locator("#mpaneSettings").is_hidden()
    text = page.locator("#sysEphemList").inner_text()
    assert "bad.bsp" in text and "ok.bsp" in text
    bad_cb = page.locator('#sysEphemList input[value="bad.bsp"]')
    assert bad_cb.is_checked() and bad_cb.is_disabled()
    assert not page.locator('#sysEphemList input[value="ok.bsp"]').is_disabled()


def _json_route(route, payload, status=200):
    route.fulfill(status=status, content_type="application/json; charset=utf-8",
                  body=json.dumps(payload, ensure_ascii=False))


def test_ephemeris_search_download_delete(page, live_server):
    from orbcalc import COMPUTE_DEFAULTS

    title, default_cfg = _evvejs_default()
    state = {"ephemerides": []}

    def handler(route):
        url = route.request.url.split("?", 1)[0].removeprefix(live_server)
        method = route.request.method
        if url == "/api/health":
            return _json_route(route, {"host": "127.0.0.1", "port": 8765, "describe": "t", "lan": False})
        if url == "/api/presets":
            return _json_route(route, {title: default_cfg})
        if url == "/api/compcfg":
            return _json_route(route, {**COMPUTE_DEFAULTS, "jobs": 1, "era_step_d": 10})
        if url == "/api/jobs":
            return _json_route(route, [])
        if url == "/api/bodies":
            return _json_route(route, {"EARTH": {"label": "Earth", "note": "", "builtin": True}})
        if url == "/api/sysconfig" and method == "GET":
            return _json_route(route, {
                "host": "127.0.0.1", "port": 8765, "open_browser": True,
                "ephemerides": state["ephemerides"], "ephemeris_span": [4000.0, 5000.0],
                "ephemeris_options": [{"file": f, "size": 1000, "n_bodies": 1, "coverage": None}
                                      for f in state["ephemerides"]]})
        if url == "/api/horizons/search":
            return _json_route(route, {"results": [
                {"kind": "small_body", "naif": 2162173, "label": "162173 Ryugu",
                 "orbit_class": "Apollo", "horizons": "162173;"}]})
        if url == "/api/ephemeris/add" and method == "POST":
            state["ephemerides"].append("ryugu.bsp")
            return _json_route(route, {"task_id": "t1"})
        if url == "/api/ephemeris/task/t1":
            return _json_route(route, {"status": "done", "result": {
                "file": "ryugu.bsp", "naif": 2162173, "name": "Ryugu",
                "radius_m": 448.0, "coverage": [4000, 5000], "warning": None}})
        if url.startswith("/api/ephemeris/") and method == "DELETE":
            fn = url.rsplit("/", 1)[1]
            state["ephemerides"] = [f for f in state["ephemerides"] if f != fn]
            return _json_route(route, {"deleted": fn})
        return _json_route(route, {"error": "unexpected " + url}, status=404)

    page.route("**/api/**", handler)
    page.on("dialog", lambda d: d.accept())      # 删除确认框
    page.goto(live_server)
    page.wait_for_selector("#seqNodes .node-row")

    _open_ephemeris(page)
    page.locator("#sysHorizonsQuery").fill("Ryugu")
    page.locator("#sysHorizonsSearch").click()
    page.wait_for_selector("#sysHorizonsResults .eph-result")
    assert "Ryugu" in page.locator("#sysHorizonsResults").inner_text()

    page.locator("#sysHorizonsDownload").click()
    page.wait_for_function(
        "() => [...document.querySelectorAll('#sysEphemList .eph-name')]"
        ".some(e => e.textContent.includes('ryugu.bsp'))")

    page.locator("#sysEphemList .eph-del").first.click()
    page.wait_for_function(
        "() => ![...document.querySelectorAll('#sysEphemList .eph-name')]"
        ".some(e => e.textContent.includes('ryugu.bsp'))")
