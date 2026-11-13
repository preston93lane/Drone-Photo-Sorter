import json
import threading

import pytest

from drone_sorter.sorter import (
    DEFAULT_CONFIG, UNSORTED, Category, assign_category, categories_from_config,
    find_images, sort_photos,
)
from helpers import make_jpg

CATS = categories_from_config(DEFAULT_CONFIG)


def tree(root):
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*")
                  if p.is_file() and not p.name.startswith("Sort log"))


@pytest.mark.parametrize("pitch,expected", [
    (-40.0, "Tower Flight"), (-40.5, "Tower Flight"), (-39.5, "Tower Flight"),
    (-40.6, UNSORTED), (20.0, "Uplook"), (0.0, "Center In"), (-45.0, "Compound Flight"),
    (-20.0, "Compound Flight"), (-25.0, "Tower Climb"), (-90.0, "Overall"),
    (-35.0, "Downlook"), (-10.0, UNSORTED), (None, UNSORTED),
])
def test_assign_category_matches_v5_ranges(pitch, expected):
    assert assign_category(pitch, CATS) == expected


def test_tolerance_override_and_explicit_ranges():
    cats = categories_from_config(DEFAULT_CONFIG, tolerance=2)
    assert assign_category(-41.9, cats) == "Tower Flight"
    cats = categories_from_config({"categories": [{"name": "Wide", "min": -10, "max": -30}]})
    assert cats == [Category("Wide", -30, -10)]


@pytest.fixture
def flight(tmp_path):
    root = tmp_path / "Flight1"
    make_jpg(root / "DJI_0001.JPG", "-40.00")
    make_jpg(root / "DJI_0002.JPG", "+20.00")
    make_jpg(root / "DJI_0003.JPG", None)
    make_jpg(root / "sub" / "DJI_0004.jpeg", "-90.0")
    (root / "notes.txt").write_text("ignore me")
    return root


def test_copy_sort(flight):
    results = sort_photos([flight], CATS)
    assert {r.action for r in results} == {"copied"}
    assert tree(flight) == [
        "DJI_0001.JPG", "DJI_0002.JPG", "DJI_0003.JPG",
        "Tower Flight/DJI_0001.JPG", "UNSORTED/DJI_0003.JPG", "Uplook/DJI_0002.JPG",
        "notes.txt", "sub/DJI_0004.jpeg", "sub/Overall/DJI_0004.jpeg",
    ]


def test_rerun_is_idempotent(flight):
    sort_photos([flight], CATS)
    before = tree(flight)
    results = sort_photos([flight], CATS)
    assert len(results) == 4  # output folders were not re-scanned
    assert {r.action for r in results} == {"skipped"}
    assert tree(flight) == before


def test_name_collision_with_different_file_gets_renamed(flight):
    make_jpg(flight / "Tower Flight" / "DJI_0001.JPG", "-40.00", payload=b"other photo")
    results = sort_photos([flight], CATS)
    r = next(r for r in results if r.source.name == "DJI_0001.JPG")
    assert r.action == "copied" and r.dest.name == "DJI_0001_1.JPG"


def test_move(flight):
    results = sort_photos([flight], CATS, move=True)
    assert {r.action for r in results} == {"moved"}
    assert tree(flight) == [
        "Tower Flight/DJI_0001.JPG", "UNSORTED/DJI_0003.JPG", "Uplook/DJI_0002.JPG",
        "notes.txt", "sub/Overall/DJI_0004.jpeg",
    ]


def test_dry_run_changes_nothing(flight):
    before = tree(flight)
    results = sort_photos([flight], CATS, dry_run=True)
    assert {r.action for r in results} == {"planned"}
    assert tree(flight) == before


def test_cancel_stops_early(flight):
    ev = threading.Event()
    seen = []

    def progress(i, n, r):
        seen.append(i)
        ev.set()

    results = sort_photos([flight], CATS, on_progress=progress, cancel=ev)
    assert len(results) == 1


def test_find_images_skips_output_folders_at_any_depth(flight):
    make_jpg(flight / "UNSORTED" / "deep" / "x.jpg", "-40")
    names = {c.name for c in CATS} | {UNSORTED}
    assert all("UNSORTED" not in p.parts for p in find_images([flight], names))


def test_progress_callback_counts(flight):
    calls = []
    sort_photos([flight], CATS, dry_run=True, on_progress=lambda i, n, r: calls.append((i, n)))
    assert calls == [(1, 4), (2, 4), (3, 4), (4, 4)]


def test_cli_dry_run(flight, capsys, tmp_path):
    from drone_sorter.sorter import main
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps(DEFAULT_CONFIG))
    assert main([str(flight), "--dry-run", "--config", str(cfg)]) == 0
    assert "Tower Flight: 1" in capsys.readouterr().out
