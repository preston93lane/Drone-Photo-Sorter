"""Move must never lose a photo. These tests try hard to make it do so."""

import os

import pytest

from drone_sorter import sorter
from drone_sorter.sorter import (
    DEFAULT_CONFIG, categories_from_config, safe_move, sort_photos, undo_sort_log,
)
from helpers import make_jpg

CATS = categories_from_config(DEFAULT_CONFIG)


def all_bytes(root):
    """Multiset of every photo's contents under root (names don't matter)."""
    return sorted(p.read_bytes() for p in root.rglob("*") if p.suffix.lower() in {".jpg", ".dng"})


@pytest.fixture(params=["hardlink", "copy-verify"])
def fallback(request, monkeypatch):
    """Run every test both with hard links and with the copy+verify path
    (what happens on an exFAT SD card on Mac/Linux)."""
    if request.param == "copy-verify":
        def no_link(*a, **k):
            raise OSError("links not supported")
        monkeypatch.setattr(sorter.os, "link", no_link)
    return request.param


def test_safe_move_refuses_to_overwrite(tmp_path, fallback):
    a = make_jpg(tmp_path / "a.jpg", "-40", payload=b"A")
    b = make_jpg(tmp_path / "b.jpg", "-40", payload=b"B")
    with pytest.raises(FileExistsError):
        safe_move(a, b)
    assert a.read_bytes().endswith(b"A\xff\xd9") and b.read_bytes().endswith(b"B\xff\xd9")


def test_failed_verification_keeps_original(tmp_path, monkeypatch):
    monkeypatch.setattr(sorter.os, "link", lambda *a: (_ for _ in ()).throw(OSError("no links")))
    monkeypatch.setattr(sorter, "same_bytes", lambda a, b: False)
    a = make_jpg(tmp_path / "a.jpg", "-40")
    original = a.read_bytes()
    with pytest.raises(OSError):
        safe_move(a, tmp_path / "out.jpg")
    assert a.read_bytes() == original
    assert not (tmp_path / "out.jpg").exists()  # partial copy cleaned up


def test_copy_crash_midway_keeps_original(tmp_path, monkeypatch):
    monkeypatch.setattr(sorter.os, "link", lambda *a: (_ for _ in ()).throw(OSError("no links")))

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(sorter.shutil, "copyfileobj", boom)
    a = make_jpg(tmp_path / "a.jpg", "-40")
    original = a.read_bytes()
    results = sort_photos([tmp_path], CATS, move=True)
    assert results[0].action == "error"
    assert a.read_bytes() == original


def test_move_with_name_collisions_loses_nothing(tmp_path, fallback):
    root = tmp_path / "Flight"
    for i in range(20):
        make_jpg(root / f"DJI_{i:04}.JPG", ["-40.00", "+20.00", "-7.0"][i % 3], payload=bytes([i]) * 50)
    # Pre-existing different files with the same names in the destinations
    make_jpg(root / "Tower Flight" / "DJI_0000.JPG", "-40.00", payload=b"older photo")
    make_jpg(root / "Tower Flight" / "DJI_0000_1.JPG", "-40.00", payload=b"another older")
    before = all_bytes(root)

    results = sort_photos([root], CATS, move=True)
    assert all(r.action == "moved" for r in results)
    assert all_bytes(root) == before  # every photo still exists exactly once


def test_identical_already_sorted_original_is_left_alone(tmp_path, fallback):
    root = tmp_path / "Flight"
    src = make_jpg(root / "DJI_0001.JPG", "-40.00")
    make_jpg(root / "Tower Flight" / "DJI_0001.JPG", "-40.00")
    results = sort_photos([root], CATS, move=True)
    assert results[0].action == "skipped"
    assert src.exists()  # never deleted, even though an identical copy exists


def test_sort_log_and_undo_restore_everything(tmp_path, fallback):
    root = tmp_path / "Flight"
    for i in range(6):
        make_jpg(root / f"DJI_{i:04}.JPG", ["-40.00", "+20.00", "-7.0"][i % 3], payload=bytes([i]))
    before = {p.name: p.read_bytes() for p in root.iterdir()}

    sort_photos([root], CATS, move=True)
    logs = list(root.glob("Sort log *.csv"))
    assert len(logs) == 1
    assert not any(p.suffix == ".JPG" for p in root.iterdir())

    statuses = {s for s, _, _ in undo_sort_log(logs[0])}
    assert statuses == {"restored"}
    after = {p.name: p.read_bytes() for p in root.iterdir() if p.suffix == ".JPG"}
    assert after == before
    assert not any(p.is_dir() for p in root.iterdir())  # empty category folders removed
    assert list(root.glob("Sort log * (undone).csv"))


def test_undo_never_overwrites(tmp_path):
    root = tmp_path / "Flight"
    make_jpg(root / "DJI_0001.JPG", "-40.00", payload=b"first")
    sort_photos([root], CATS, move=True)
    new_photo = make_jpg(root / "DJI_0001.JPG", "-40.00", payload=b"new one, same name")
    log = next(root.glob("Sort log *.csv"))
    statuses = [s for s, _, _ in undo_sort_log(log)]
    assert statuses == ["skipped"]
    assert new_photo.read_bytes().endswith(b"new one, same name\xff\xd9")
    assert (root / "Tower Flight" / "DJI_0001.JPG").exists()
    assert log.exists()  # not marked undone because something was skipped


def test_dry_run_writes_no_log(tmp_path):
    make_jpg(tmp_path / "a.jpg", "-40")
    sort_photos([tmp_path], CATS, move=True, dry_run=True)
    assert not list(tmp_path.glob("Sort log*"))
