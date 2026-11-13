"""Core sorting logic (no UI). Usable from the GUI, tests, or the command line."""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
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
#
# SAFETY RULES for everything below:
#   * An existing file is never overwritten.
#   * An original is only removed after its new copy is confirmed to exist
#     and be identical. There is no code path that deletes a photo outright.

def same_bytes(a: Path, b: Path, bufsize: int = 1024 * 1024) -> bool:
    """True if two files have identical contents (full byte comparison)."""
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            ca, cb = fa.read(bufsize), fb.read(bufsize)
            if ca != cb:
                return False
            if not ca:
                return True


def _unique_dest(dest: Path) -> Path:
    stem, suffix = dest.stem, dest.suffix
    n = 1
    while True:
        candidate = dest.with_name(f"{stem}_{n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def safe_move(src: Path, dest: Path) -> None:
    """Move ``src`` to ``dest`` without any chance of losing a photo.

    Raises FileExistsError (and changes nothing) if ``dest`` already exists.
    """
    src, dest = Path(src), Path(dest)

    # 1) Hard link + unlink. The OS refuses to create the link if dest exists,
    #    no data is copied, and the original name is removed only once the new
    #    name is confirmed to point at the very same file.
    try:
        os.link(src, dest)
    except FileExistsError:
        raise
    except (OSError, NotImplementedError, AttributeError):
        pass  # e.g. exFAT/FAT32 SD cards don't support hard links
    else:
        if not os.path.samefile(src, dest):
            raise OSError(f"Link check failed; original kept: {src}")
        os.unlink(src)
        return

    # 2) Windows: a same-drive rename is atomic and never overwrites.
    if os.name == "nt":
        os.rename(src, dest)
        return

    # 3) Fallback: copy into a brand-new file (refuses to overwrite), flush it
    #    to disk, verify it byte-for-byte, and only then remove the original.
    created = False
    try:
        with open(src, "rb") as fsrc, open(dest, "xb") as fdst:
            created = True
            shutil.copyfileobj(fsrc, fdst, 1024 * 1024)
            fdst.flush()
            os.fsync(fdst.fileno())
        shutil.copystat(src, dest)
        if not same_bytes(src, dest):
            raise OSError(f"Copy check failed; original kept: {src}")
    except BaseException:
        if created:  # only ever clean up our own partial copy, never the original
            try:
                os.unlink(dest)
            except OSError:
                pass
        raise
    os.unlink(src)


def place_file(src: Path, category: str, move: bool, dry_run: bool) -> tuple[Path, str]:
    """Put ``src`` into ``<its folder>/<category>/``. Returns (dest, action)."""
    target_dir = src.parent / category
    dest = target_dir / src.name

    if dest.exists():
        if same_bytes(src, dest):
            return dest, "skipped"          # already sorted; original left untouched
        dest = _unique_dest(dest)           # same name, different photo

    if dry_run:
        return dest, "planned"

    target_dir.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        try:
            if move:
                safe_move(src, dest)
                return dest, "moved"
            _safe_copy(src, dest)
            return dest, "copied"
        except FileExistsError:
            dest = _unique_dest(dest)       # something appeared there meanwhile
    raise OSError(f"Could not find a free file name for {src.name}")


def _safe_copy(src: Path, dest: Path) -> None:
    """copy2, but refuses to overwrite an existing file."""
    created = False
    try:
        with open(src, "rb") as fsrc, open(dest, "xb") as fdst:
            created = True
            shutil.copyfileobj(fsrc, fdst, 1024 * 1024)
        shutil.copystat(src, dest)
    except BaseException:
        if created:  # remove our own partial copy; the original is untouched
            try:
                os.unlink(dest)
            except OSError:
                pass
        raise


# ------------------------------------------------------------ sort log ---

LOG_FIELDS = ["action", "original", "new_location", "pitch", "category"]


def log_name(when: datetime) -> str:
    return f"Sort log {when:%Y-%m-%d %H-%M-%S}.csv"


class SortLog:
    """Writes one CSV per source folder, a row at a time, so the record is
    complete even if the app is closed mid-run. Used by ``undo_sort_log``."""

    def __init__(self, when: datetime):
        self.when = when
        self._files: dict[Path, tuple] = {}
        self.paths: list[Path] = []

    def add(self, root: Path, res: "FileResult") -> None:
        if root not in self._files:
            path = root / log_name(self.when)
            fh = open(path, "w", newline="", encoding="utf-8")
            writer = csv.writer(fh)
            writer.writerow(LOG_FIELDS)
            self._files[root] = (fh, writer)
            self.paths.append(path)
        fh, writer = self._files[root]
        pitch = "" if res.pitch is None else f"{res.pitch}"
        writer.writerow([res.action, str(res.source), str(res.dest), pitch, res.category])
        fh.flush()

    def close(self) -> None:
        for fh, _ in self._files.values():
            fh.close()


def undo_sort_log(log_path: Path) -> list[tuple[str, Path, str]]:
    """Put moved photos back where they came from, using a sort log.

    Returns (status, original_path, detail) per row. Copies are left alone
    (the originals were never touched). Nothing is ever overwritten: if a file
    already sits at the original location, that row is skipped.
    """
    log_path = Path(log_path)
    out = []
    with open(log_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        original, current = Path(row["original"]), Path(row["new_location"])
        if row["action"] != "moved":
            out.append(("left", original, f"was {row['action']}, nothing to undo"))
            continue
        if not current.exists():
            out.append(("missing", original, f"not found at {current}"))
            continue
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            safe_move(current, original)
            out.append(("restored", original, ""))
        except FileExistsError:
            out.append(("skipped", original, "a file already exists at the original location"))
        except OSError as e:
            out.append(("error", original, str(e)))
    for folder in {Path(r["new_location"]).parent for r in rows if r["action"] == "moved"}:
        try:
            folder.rmdir()  # only succeeds if the category folder is now empty
        except OSError:
            pass
    if all(status in ("restored", "left") for status, _, _ in out):
        log_path.rename(log_path.with_name(log_path.stem + " (undone).csv"))
    return out


# ------------------------------------------------------------------- run ---

def sort_photos(
    sources: Iterable[Path],
    categories: list[Category],
    move: bool = False,
    dry_run: bool = False,
    on_progress: Callable[[int, int, FileResult], None] | None = None,
    cancel: threading.Event | None = None,
    workers: int = 8,
    write_log: bool = True,
) -> list[FileResult]:
    """Sort every photo under ``sources``. Pitch reading runs in parallel;
    file placement is sequential so name-collision handling stays safe.

    Unless ``dry_run``, a "Sort log <date>.csv" is written into each source
    folder recording where every photo went (see ``undo_sort_log``)."""
    output_names = {c.name for c in categories} | {UNSORTED}
    jobs: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    for root in sources:
        for img in find_images([root], output_names):
            key = img.resolve()
            if key not in seen:
                seen.add(key)
                jobs.append((Path(root), img))
    total = len(jobs)
    results: list[FileResult] = []
    log = SortLog(datetime.now()) if (write_log and not dry_run) else None

    def read(job: tuple[Path, Path]) -> tuple[float | None, str]:
        try:
            return read_gimbal_pitch(job[1]), ""
        except OSError as e:
            return None, str(e)

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, ((root, img), (pitch, read_err)) in enumerate(zip(jobs, pool.map(read, jobs)), 1):
                if cancel is not None and cancel.is_set():
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                category = assign_category(pitch, categories)
                try:
                    dest, action = place_file(img, category, move, dry_run)
                    res = FileResult(img, pitch, category, dest, action, read_err)
                except OSError as e:
                    res = FileResult(img, pitch, category, None, "error", str(e))
                if log is not None and res.action in ("moved", "copied"):
                    log.add(root, res)
                results.append(res)
                if on_progress:
                    on_progress(i, total, res)
    finally:
        if log is not None:
            log.close()
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
