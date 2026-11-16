"""-50 degree Tower Flight for taller towers, and the settings-file upgrade."""

import json

import pytest

from drone_sorter.sorter import (
    DEFAULT_CONFIG, assign_category, apply_sequence_rules, categories_from_config,
    ensure_config_file, load_config, migrate_config, rules_from_config,
)

CATS = categories_from_config(DEFAULT_CONFIG)
RULES = rules_from_config(DEFAULT_CONFIG)


@pytest.mark.parametrize("p", [-50.0, -49.6, -50.4, -40.0])
def test_minus_50_and_40_are_tower_flight(p):
    assert assign_category(p, CATS) == "Tower Flight"


def test_minus_45_is_still_compound():
    assert assign_category(-45.0, CATS) == "Compound Flight"


def run(pitches):
    cats = [assign_category(p, CATS) for p in pitches]
    return apply_sequence_rules(pitches, cats, RULES)[0]


def test_minus_50_inside_compound_raise_goes_to_compound():
    assert run([-45, -50, -40, -30, -20]) == ["Compound Flight"] * 5


def test_tall_tower_shot_list():
    shots = [-50, -50, -35, 20, 0, -45, -40, -30, -20, -25, -60, -90]
    assert run(shots) == [
        "Tower Flight", "Tower Flight", "Downlook", "Uplook", "Center In",
        "Compound Flight", "Compound Flight", "Compound Flight", "Compound Flight",
        "Tower Climb", "Overall", "Overall",
    ]


def old_file(tmp_path, cats=None):
    p = tmp_path / "categories.json"
    cfg = {"tolerance": 0.7, "categories": cats if cats is not None else [
        {"name": "Tower Flight", "pitch": -40}, {"name": "My Custom", "pitch": 33}]}
    p.write_text(json.dumps(cfg))
    return p


def test_old_settings_file_gains_minus_50_once(tmp_path):
    p = old_file(tmp_path)
    ensure_config_file(p)
    cfg = json.loads(p.read_text())
    assert cfg["config_version"] == 2 and cfg["tolerance"] == 0.7
    assert cfg["categories"] == [
        {"name": "Tower Flight", "pitch": -40}, {"name": "Tower Flight", "pitch": -50},
        {"name": "My Custom", "pitch": 33}]
    ensure_config_file(p)  # second start: no duplicates
    assert json.loads(p.read_text()) == cfg


def test_removed_entry_is_not_put_back(tmp_path):
    p = tmp_path / "categories.json"
    p.write_text(json.dumps({"config_version": 2, "categories": [{"name": "Tower Flight", "pitch": -40}]}))
    ensure_config_file(p)
    assert len(json.loads(p.read_text())["categories"]) == 1


def test_existing_minus_50_not_duplicated():
    cfg = {"categories": [{"name": "Something", "pitch": -50}]}
    assert migrate_config(cfg) and len(cfg["categories"]) == 1


def test_load_config_upgrades_in_memory(tmp_path):
    cfg = load_config(old_file(tmp_path))
    assert assign_category(-50, categories_from_config(cfg)) == "Tower Flight"
