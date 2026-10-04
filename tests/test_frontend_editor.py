from __future__ import annotations

import json
from datetime import date

from playwright.sync_api import expect


def read_config(page):
    return json.loads(page.locator("#cfgJsonBox").input_value())


def assert_roles_and_leg_labels(page, seq):
    nodes = page.locator("#seqNodes .node-row")
    assert nodes.count() == len(seq)
    assert [nodes.nth(i).locator(".node-idx").inner_text() for i in range(len(seq))] == [
        str(i + 1) for i in range(len(seq))
    ]
    roles = [nodes.nth(i).locator(".node-role").inner_text() for i in range(len(seq))]
    assert roles == ["出发"] + ["飞掠"] * (len(seq) - 2) + ["到达"]

    rows = page.locator("#tofTable table tr")
    assert rows.count() == len(seq)  # header + one row per adjacent-node leg
    labels = [rows.nth(i + 1).locator("td").nth(1).inner_text().strip() for i in range(len(seq) - 1)]
    assert labels == [f"{seq[i]} → {seq[i + 1]}" for i in range(len(seq) - 1)]


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

    page.locator("#nodeAdd").click()
    added = read_config(page)
    expected = original_seq[:-1] + ["VENUS", original_seq[-1]]
    assert added["seq"] == expected
    assert len(added["tof_bounds"]) == len(expected) - 1
    assert added["tof_bounds"][:len(original_bounds)] == original_bounds
    assert_roles_and_leg_labels(page, expected)

    inserted_index = len(expected) - 2
    inserted = page.locator("#seqNodes .node-row").nth(inserted_index)
    inserted.locator('select[data-k="tag"]').select_option("MARS")
    expected[inserted_index] = "MARS"
    assert read_config(page)["seq"] == expected
    assert_roles_and_leg_labels(page, expected)

    # Move the inserted node across Jupiter and verify that visible labels and
    # the serialized sequence move together.
    inserted = page.locator("#seqNodes .node-row").nth(inserted_index)
    inserted.locator(".node-move.up").click()
    expected[inserted_index - 1], expected[inserted_index] = expected[inserted_index], expected[inserted_index - 1]
    assert read_config(page)["seq"] == expected
    assert_roles_and_leg_labels(page, expected)

    inserted = page.locator("#seqNodes .node-row").nth(inserted_index - 1)
    inserted.locator(".node-move.dn").click()
    expected[inserted_index - 1], expected[inserted_index] = expected[inserted_index], expected[inserted_index - 1]
    assert read_config(page)["seq"] == expected
    assert_roles_and_leg_labels(page, expected)

    page.locator("#seqNodes .node-row").nth(inserted_index).locator(".node-del").click()
    final = read_config(page)
    assert final["seq"] == original_seq
    assert final["tof_bounds"] == original_bounds
    assert_roles_and_leg_labels(page, original_seq)
