"""Core sorting logic (no UI). Usable from the GUI, tests, or the command line."""

from __future__ import annotations

import filecmp
import json
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .pitch_reader import read_gimbal_pitch

UNSORTED = "UNSORTED"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".dng"}

# Same targets as v5: each category matches pitch = center +/- tolerance.
DEFAULT_CONFIG = {
    "tolerance": 0.5,
    "categories": [
        {"name": "Tower Flight", "pitch": -40},
        {"name": "Downlook", "pitch": -35},
        {"name": "Uplook", "pitch": 20},
        {"name": "Center In", "pitch": 0},
        {"name": "Compound Flight", "pitch": -45},
        {"name": "Compound Flight", "pitch": -20},
        {"name": "Tower Climb", "pitch": -25},
        {"name": "Overall", "pitch": -90},
    ],
}


@dataclass(frozen=True)
class Category:
    name: str
    min: float
    max: float

    def matches(self, pitch: float) -> bool:
        return self.min <= pitch <= self.max


@dataclass
class FileResult:
    source: Path
    pitch: float | None
    category: str
    dest: Path | None
    action: str  # copied | moved | skipped | planned | error
    error: str = ""


# ---------------------------------------------------------------- config ---

def app_dir() -> Path:
    """Folder next to the .exe when frozen, otherwise the project root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return app_dir() / "categories.json"


def ensure_config_file(path: Path | None = None) -> Path:
    """Create categories.json with the defaults if it doesn't exist yet."""
    path = path or config_path()
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    return path


def categories_from_config(cfg: dict, tolerance: float | None = None) -> list[Category]:
    """Build categories from a config dict.

    Each entry is either ``{"name", "pitch"}`` (uses the tolerance, which can be
    overridden per entry with ``"tolerance"``) or an explicit ``{"name", "min", "max"}``.
    """
    tol_default = float(cfg.get("tolerance", 0.5) if tolerance is None else tolerance)
    cats = []
    for entry in cfg.get("categories", []):
        name = str(entry["name"]).strip()
        if "min" in entry and "max" in entry:
            lo, hi = float(entry["min"]), float(entry["max"])
        else:
            tol = float(entry.get("tolerance", tol_default))
            center = float(entry["pitch"])
            lo, hi = center - tol, center + tol
        cats.append(Category(name, min(lo, hi), max(lo, hi)))
    return cats


def load_config(path: Path | None = None) -> dict:
    path = path or config_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DEFAULT_CONFIG


def assign_category(pitch: float | None, categories: Iterable[Category]) -> str:
    if pitch is None:
        return UNSORTED
    for cat in categories:
        if cat.matches(pitch):
            return cat.name
    return UNSORTED


# ------------------------------------------------------------- discovery ---

def find_images(sources: Iterable[Path], output_names: set[str]) -> list[Path]:
    """All photos under the sources, skipping anything inside our own output
    folders at any depth, so re-running a folder never re-sorts its own output."""
    found: list[Path] = []
    seen: set[Path] = set()
    for src in sources:
        src = Path(src)
        for p in sorted(src.rglob("*")):
            if p.suffix.lower() not in IMAGE_EXTENSIONS or not p.is_file():
                continue
            rel_parts = p.relative_to(src).parts[:-1]
            if any(part in output_names for part in rel_parts):
                continue
            key = p.resolve()
            if key not in seen:
                seen.add(key)
                found.append(p)
    return found


# ------------------------------------------------------------- placement ---

def _unique_dest(dest: Path) -> Path:
    stem, suffix = dest.stem, dest.suffix
    n = 1
    while True:
        candidate = dest.with_name(f"{stem}_{n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def place_file(src: Path, category: str, move: bool, dry_run: bool) -> tuple[Path, str]:
    """Put ``src`` into ``<its folder>/<category>/``. Returns (dest, action)."""
    target_dir = src.parent / category
    dest = target_dir / src.name

    if dest.exists():
        if filecmp.cmp(src, dest, shallow=False):
            return dest, "skipped"          # already sorted, identical file
        dest = _unique_dest(dest)           # same name, different photo

    if dry_run:
        return dest, "planned"

    target_dir.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(src), str(dest))
        return dest, "moved"
    shutil.copy2(src, dest)
    return dest, "copied"


# ------------------------------------------------------------------- run ---

def sort_photos(
    sources: Iterable[Path],
    categories: list[Category],
    move: bool = False,
    dry_run: bool = False,
    on_progress: Callable[[int, int, FileResult], None] | None = None,
    cancel: threading.Event | None = None,
    workers: int = 8,
) -> list[FileResult]:
    """Sort every photo under ``sources``. Pitch reading runs in parallel;
    file placement is sequential so name-collision handling stays safe."""
    output_names = {c.name for c in categories} | {UNSORTED}
    images = find_images(sources, output_names)
    total = len(images)
    results: list[FileResult] = []

    def read(p: Path) -> tuple[float | None, str]:
        try:
            return read_gimbal_pitch(p), ""
        except OSError as e:
            return None, str(e)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (img, (pitch, read_err)) in enumerate(zip(images, pool.map(read, images)), 1):
            if cancel is not None and cancel.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                break
            category = assign_category(pitch, categories)
            try:
                dest, action = place_file(img, category, move, dry_run)
                res = FileResult(img, pitch, category, dest, action, read_err)
            except OSError as e:
                res = FileResult(img, pitch, category, None, "error", str(e))
            results.append(res)
            if on_progress:
                on_progress(i, total, res)
    return results


def main(argv: list[str] | None = None) -> int:
    """Minimal command-line mode: python -m drone_sorter.sorter FOLDER [--move] [--dry-run]"""
    import argparse
    from collections import Counter

    ap = argparse.ArgumentParser(description="Sort DJI photos by gimbal pitch.")
    ap.add_argument("folders", nargs="+", type=Path)
    ap.add_argument("--move", action="store_true", help="move instead of copy")
    ap.add_argument("--dry-run", action="store_true", help="only show what would happen")
    ap.add_argument("--config", type=Path, help="categories.json to use")
    args = ap.parse_args(argv)

    cats = categories_from_config(load_config(args.config))
    results = sort_photos(args.folders, cats, move=args.move, dry_run=args.dry_run)
    for r in results:
        pitch = f"{r.pitch:.1f}" if r.pitch is not None else "N/A"
        print(f"{r.action:8} {pitch:>7}  {r.source} -> {r.category}/")
    print()
    for cat, n in sorted(Counter(r.category for r in results).items()):
        print(f"  {cat}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
