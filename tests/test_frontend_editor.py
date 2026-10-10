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
    labels = [rows.nth(i + 1).locator("td").nth(0).inner_text().strip() for i in range(len(seq) - 1)]
    assert labels == [f"{seq[i]} → {seq[i + 1]}" for i in range(len(seq) - 1)]
    assert page.locator("#tofTable .leg-lam").count() == len(seq) - 1


def test_layout_collapse_and_result_view_toggles(editor_page):
    page = editor_page
    layout = page.locator(".layout")

    # 任务配置面板可折叠
    page.locator("#configToggle").click()
    assert layout.evaluate("el => el.classList.contains('config-collapsed')")
    page.locator("#configToggle").click()
    assert not layout.evaluate("el => el.classList.contains('config-collapsed')")

    # 结果表首行的切换控件 (T+/Datetime, Total/RTN/RPN) — 注入假结果后渲染
    page.locator("#tabResult").click()
    page.evaluate("""() => {
      state.lastResult = {status: "ok", sequence: ["Earth", "Venus"],
        legs: [{from: "Earth", to: "Venus", dsm_elapsed_d: 10.0,
                dsm_iso: "2020-01-01 00:00:00", dsm_ms: 100.0,
                dsm_rtn_ms: [1.0, 2.0, 3.0], dsm_rpn_ms: [4.0, 5.0, 6.0]}],
        flybys: []};
      renderTables();
    }""")
    page.locator(".seg[data-k='timeMode'] button[data-v='datetime']").click()
    assert page.locator(".seg[data-k='timeMode'] button[data-v='datetime']").evaluate(
        "b => b.classList.contains('active')")
    # Datetime 模式下第一条腿的时间单元格显示日期时间
    assert "2020-01-01" in page.locator("#legTable table tr").nth(1).inner_text()
    # 飞掠表的到达时间开关独立, 不随转移表 timeMode 联动 (仍为 T+)
    assert page.locator(".seg[data-k='arriveMode'] button[data-v='elapsed']").evaluate(
        "b => b.classList.contains('active')")
    page.locator(".seg[data-k='dsmMode'] button[data-v='rpn']").click()
    assert page.locator(".seg[data-k='dsmMode'] button[data-v='rpn']").evaluate(
        "b => b.classList.contains('active')")
    # RPN 模式下第一条腿的 DSM 单元格显示 R/P/N
    assert "R 4.00" in page.locator("#legTable table tr").nth(1).inner_text()


def test_add_and_delete_eras_preserves_order_and_minimum_one(editor_page):
    page = editor_page
    assert page.locator("#cfgName").input_value() == ""
    assert page.locator("#cfgName").get_attribute("placeholder") == "Auto-generated"
    original = read_config(page)["eras"]

    page.locator("#eraAdd").click()
    after_one = read_config(page)["eras"]
    assert after_one[0] == original[0]
    assert after_one[1][0] == original[-1][1]
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


def test_add_move_and_delete_body_keeps_sequence_and_tof_legs_aligned(editor_page):
    page = editor_page
    original = read_config(page)
    original_seq = original["seq"]
    original_bounds = original["tof_bounds"]

    # 行为变更: 新节点追加到末尾 (成为新的“到达”)
    page.locator("#nodeAdd").click()
    added = read_config(page)
    expected = original_seq + ["VENUS"]
    assert added["seq"] == expected
    assert len(added["tof_bounds"]) == len(expected) - 1
    assert added["tof_bounds"][:len(original_bounds)] == original_bounds
    assert_roles_and_leg_labels(page, expected)

    last_index = len(expected) - 1
    last = page.locator("#seqNodes .node-row").nth(last_index)
    last.locator('select[data-k="tag"]').select_option("MARS")
    expected[last_index] = "MARS"
    assert read_config(page)["seq"] == expected
    assert_roles_and_leg_labels(page, expected)

    # 原“到达”现在变成飞掠, 验证移动后可见标签与序列号同步
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

    # 删除追加的末尾节点, 恢复原始序列与 TOF 边界
    page.locator("#seqNodes .node-row").nth(last_index).locator(".node-del").click()
    final = read_config(page)
    assert final["seq"] == original_seq
    assert final["tof_bounds"] == original_bounds
    assert_roles_and_leg_labels(page, original_seq)


def test_departure_and_arrival_nodes_can_move(editor_page):
    page = editor_page
    seq = read_config(page)["seq"]

    # 出发点可下移 (与第二个节点交换)
    page.locator("#seqNodes .node-row").nth(0).locator(".node-move.dn").click()
    expected = [seq[1], seq[0]] + seq[2:]
    assert read_config(page)["seq"] == expected
    assert_roles_and_leg_labels(page, expected)

    # 到达点可上移
    last = len(expected) - 1
    page.locator("#seqNodes .node-row").nth(last).locator(".node-move.up").click()
    expected[last - 1], expected[last] = expected[last], expected[last - 1]
    assert read_config(page)["seq"] == expected
    assert_roles_and_leg_labels(page, expected)


def leg_lam(page, leg):
    return page.locator(f'#tofTable .leg-lam[data-leg="{leg}"]')


def test_transfer_type_button_is_in_tof_table_and_cycles(editor_page):
    page = editor_page
    n_legs = len(read_config(page)["seq"]) - 1
    assert read_config(page)["lambert_types"] == [0] * n_legs
    # 表头无 "腿" 列, 类型列紧跟区间列
    headers = [th.inner_text().strip() for th in page.locator("#tofTable tr").first.locator("th").all()]
    assert headers == ["区间", "类型", "最小 (d)", "最大 (d)"]
    assert page.locator("#tofTable .leg-lam").count() == n_legs

    btn = leg_lam(page, 0)
    expect(btn).to_have_text("1/2")
    btn.click()
    expect(btn).to_have_text("3")
    assert read_config(page)["lambert_types"][0] == 1
    btn.click()
    expect(btn).to_have_text("4")
    assert read_config(page)["lambert_types"][0] == 2
    btn.click()
    expect(btn).to_have_text("1/2")
    assert read_config(page)["lambert_types"] == [0] * n_legs


def test_transfer_types_are_per_leg_index_preserved_on_node_changes(editor_page):
    page = editor_page
    n_legs = len(read_config(page)["seq"]) - 1

    leg_lam(page, 1).click()                       # 第 2 腿 -> 3
    assert read_config(page)["lambert_types"][1] == 1

    page.locator("#nodeAdd").click()               # 追加节点: 新增末腿默认 0, 已有保留
    assert read_config(page)["lambert_types"] == [0, 1] + [0] * (n_legs - 1)

    page.locator("#seqNodes .node-row").last.locator(".node-del").click()   # 删回
    assert read_config(page)["lambert_types"] == [0, 1] + [0] * (n_legs - 2)


def test_loading_a_config_restores_mixed_transfer_types(editor_page):
    page = editor_page
    page.evaluate("""() => fillTrajForm({
      name: "", seq: ["EARTH", "VENUS", "EARTH"],
      tof_bounds: [[300, 800], [300, 800]], lambert_types: [1, 2],
      eras: [["2029-01-01", "2030-06-30"]], objective: "min_dsm",
      objective_weights: [1, 0], penalty: [10, 0.2], frontier_penalty: [30, 2],
      dsm_limit_ms: 600, vinf_bounds_kmps: [3.5, 6], eta_bounds: [0.01, 0.9], rp_ub: 200 })""")
    expect(leg_lam(page, 0)).to_have_text("3")
    expect(leg_lam(page, 1)).to_have_text("4")
    assert read_config(page)["lambert_types"] == [1, 2]


def _mock_presets_page(page, live_server, presets):
    """Serve the mocked API (with the given presets) and navigate to the app."""
    import copy

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
    return copy


def _evvejs_default():
    import copy

    from orbcalc import TRAJ_DEFAULTS

    raw = json.loads((ROOT / "presets" / "traj_evvejs_cassini.json").read_text(encoding="utf-8"))
    cfg = copy.deepcopy(TRAJ_DEFAULTS)
    cfg.update(raw)
    return raw["title"], cfg


def test_default_preset_is_loaded_even_when_not_first(page, live_server):
    import copy

    from orbcalc import TRAJ_DEFAULTS

    title, default_cfg = _evvejs_default()
    other = copy.deepcopy(TRAJ_DEFAULTS)
    other.update({"seq": ["EARTH", "MARS"], "tof_bounds": [[100, 200]],
                  "eras": [["2029-01-01", "2030-01-01"]]})
    # 默认项故意排在后面 (dict 顺序即 API 返回顺序)
    presets = {"AAA other": other, title: default_cfg}

    _mock_presets_page(page, live_server, presets)
    page.wait_for_function(f"() => document.querySelector('#presetSelect').value === {title!r}")
    assert page.locator("#presetSelect").input_value() == title
    assert read_config(page)["seq"] == default_cfg["seq"]


def test_default_preset_falls_back_to_first_when_absent(page, live_server):
    import copy

    from orbcalc import TRAJ_DEFAULTS

    other = copy.deepcopy(TRAJ_DEFAULTS)
    other.update({"seq": ["EARTH", "MARS"], "tof_bounds": [[100, 200]],
                  "eras": [["2029-01-01", "2030-01-01"]]})

    _mock_presets_page(page, live_server, {"AAA other": other})
    page.wait_for_function("() => document.querySelector('#presetSelect').value === 'AAA other'")
    assert read_config(page)["seq"] == ["EARTH", "MARS"]
