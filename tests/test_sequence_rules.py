"""Shot-order rules: in-between photos follow the shot list."""

from drone_sorter.sorter import (
    DEFAULT_CONFIG, UNSORTED, SequenceRule, apply_sequence_rules, assign_category,
    categories_from_config, rules_from_config, sort_photos,
)
from helpers import make_jpg

CATS = categories_from_config(DEFAULT_CONFIG)
RULES = rules_from_config(DEFAULT_CONFIG)


def run(pitches, rules=RULES):
    cats = [assign_category(p, CATS) for p in pitches]
    return apply_sequence_rules(pitches, cats, rules)[0]


def test_compound_raise_goes_to_compound_flight():
    # Including angles that would otherwise match Tower Flight/Downlook/Tower Climb
    shots = [-45, -45, -42, -40, -35, -30, -25, -22, -20, -20]
    assert run(shots) == ["Compound Flight"] * 10


def test_anchor_shot_between_climb_and_overall_goes_to_overall():
    shots = [-25, -25, -60, -90, -90]
    assert run(shots) == ["Tower Climb", "Tower Climb", "Overall", "Overall", "Overall"]


def test_full_shot_list_order():
    shots = [-40, -40, -35, -35, 20, 0, -45, -45, -38, -31, -20, -25, -25, -12.3, -90]
    assert run(shots) == [
        "Tower Flight", "Tower Flight", "Downlook", "Downlook", "Uplook", "Center In",
        "Compound Flight", "Compound Flight", "Compound Flight", "Compound Flight", "Compound Flight",
        "Tower Climb", "Tower Climb", "Overall", "Overall",
    ]


def test_no_anchor_before_means_no_change():
    assert run([-30, -20]) == [UNSORTED, "Compound Flight"]


def test_gap_too_long_is_left_alone():
    rule = [SequenceRule("Compound Flight", -45, -20, max_between=3)]
    assert run([-45, -30, -30, -30, -30, -20], rule)[1:5] == [UNSORTED] * 4


def test_does_not_reach_past_an_earlier_before_run():
    # -45 ... -20 ... (stray) ... -20 : second -20 run must not grab back to the -45
    shots = [-45, -20, -10, -20]
    assert run(shots) == ["Compound Flight", "Compound Flight", UNSORTED, "Compound Flight"]


def test_old_config_without_rules_gets_defaults_and_empty_list_disables():
    assert rules_from_config({"categories": []}) == RULES
    assert rules_from_config({"sequence_rules": []}) == []


def test_rules_are_per_folder_and_in_file_name_order(tmp_path):
    a, b = tmp_path / "002", tmp_path / "003"
    # 002 ends with -45, 003 starts with -30 then -20: must NOT be linked across folders
    make_jpg(a / "DJI_0001.JPG", "-45.0")
    make_jpg(b / "DJI_0002.JPG", "-30.0")
    make_jpg(b / "DJI_0003.JPG", "-20.0")
    # within one folder, files created out of order still use name order
    make_jpg(a / "DJI_0010.JPG", "-20.0")
    make_jpg(a / "DJI_0005.JPG", "-33.0")
    res = {r.source.name: r for r in sort_photos([a, b], CATS, dry_run=True, rules=RULES)}
    assert res["DJI_0002.JPG"].category == UNSORTED
    assert res["DJI_0005.JPG"].category == "Compound Flight"
    assert res["DJI_0005.JPG"].rule == "between -45° and -20°"
    assert (b / "DJI_0002.JPG").exists()
