"""Lossless "shrink for upload" for JPG photos.

Uses jpegtran to rebuild each JPG's internal compression tables (optimized
Huffman coding, progressive layout). The image data itself (the DCT
coefficients) is not touched, so the decoded pixels are identical. All
metadata, including DJI's angle/GPS data, is copied.

Safety: the shrunk file is written next to the original, decoded and compared
pixel-by-pixel with the original, and checked to still carry the same DJI
data. Only then does it replace the original. If anything is off, the
original is left exactly as it was.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .pitch_reader import read_photo_meta

JPEG_EXTENSIONS = {".jpg", ".jpeg"}


def find_jpegtran() -> str | None:
    """The bundled jpegtran.exe in the app, else one on this computer."""
    env = os.environ.get("DRONE_SORTER_JPEGTRAN")
    if env and Path(env).is_file():
        return env
    base = getattr(sys, "_MEIPASS", None)
    if base:
        for name in ("jpegtran.exe", "jpegtran"):
            p = Path(base) / "jpegtran" / name
            if p.is_file():
                return str(p)
    return shutil.which("jpegtran")


def is_available() -> bool:
    return find_jpegtran() is not None


def _run_jpegtran(src: Path, out: Path) -> None:
    exe = find_jpegtran()
    if not exe:
        raise OSError("jpegtran isn't available")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.run(
        [exe, "-copy", "all", "-optimize", "-progressive", "-outfile", str(out), str(src)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags,
    )
    if proc.returncode != 0 or not out.exists():
        raise OSError(proc.stderr.decode(errors="replace").strip() or "jpegtran failed")


def same_pixels(a: Path, b: Path) -> bool:
    from PIL import Image
    with Image.open(a) as ia, Image.open(b) as ib:
        if ia.size != ib.size or ia.mode != ib.mode:
            return False
        return ia.tobytes() == ib.tobytes()


@dataclass
class ShrinkResult:
    path: Path
    before: int
    after: int
    status: str  # shrunk | unchanged | error
    error: str = ""

    @property
    def saved(self) -> int:
        return self.before - self.after


def shrink_file(path: Path) -> ShrinkResult:
    """Losslessly shrink one JPG in place. Never loses the original."""
    path = Path(path)
    before = path.stat().st_size
    tmp = path.with_name(path.name + ".shrinking.tmp")
    try:
        _run_jpegtran(path, tmp)
        after = tmp.stat().st_size
        if after >= before:
            tmp.unlink()
            return ShrinkResult(path, before, before, "unchanged")
        if not same_pixels(path, tmp):
            raise OSError("pixel check failed; original kept")
        if read_photo_meta(path) != read_photo_meta(tmp):
            raise OSError("DJI data check failed; original kept")
        os.replace(tmp, path)  # atomic: the original is swapped only after every check passed
        return ShrinkResult(path, before, after, "shrunk")
    except Exception as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        return ShrinkResult(path, before, before, "error", str(e))


def shrink_files(
    paths: Iterable[Path],
    on_progress: Callable[[int, int, ShrinkResult], None] | None = None,
    cancel=None,
    workers: int | None = None,
) -> list[ShrinkResult]:
    jobs = [Path(p) for p in paths if Path(p).suffix.lower() in JPEG_EXTENSIONS]
    workers = workers or max(2, min(8, (os.cpu_count() or 4)))
    results: list[ShrinkResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, res in enumerate(pool.map(shrink_file, jobs), 1):
            results.append(res)
            if on_progress:
                on_progress(i, len(jobs), res)
            if cancel is not None and cancel.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                break
    return results


def estimate_savings(photos: list[Path], sample: int = 20) -> tuple[float, int, int]:
    """Try a sample of photos (without changing them).
    Returns (fraction saved, total bytes of all photos, estimated bytes saved)."""
    jpgs = [p for p in photos if p.suffix.lower() in JPEG_EXTENSIONS]
    if not jpgs:
        return 0.0, 0, 0
    picked = random.Random(1).sample(jpgs, min(sample, len(jpgs)))
    before = after = 0
    with tempfile.TemporaryDirectory() as td:
        for i, p in enumerate(picked):
            out = Path(td) / f"{i}.jpg"
            _run_jpegtran(p, out)
            before += p.stat().st_size
            after += min(out.stat().st_size, p.stat().st_size)
    frac = (before - after) / before if before else 0.0
    total = sum(p.stat().st_size for p in jpgs)
    return frac, total, int(total * frac)
