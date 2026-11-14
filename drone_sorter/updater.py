"""Self-update from GitHub Releases.

On start the app asks GitHub for the latest release. If it's newer, the user can
click "Update now": the new exe is downloaded next to the current one, checked,
swapped in (Windows lets a running exe be renamed), and relaunched.
Settings (categories.json) are never touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__

REPO = "preston93lane/Drone-Photo-Sorter"
ASSET_NAME = "DronePitchSorter.exe"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
USER_AGENT = f"DronePitchSorter/{__version__}"


@dataclass
class Release:
    version: str
    url: str
    size: int
    notes: str = ""


def parse_version(text: str) -> tuple[int, ...]:
    text = text.strip().lstrip("vV")
    parts = []
    for piece in text.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def can_self_update() -> bool:
    """Only the packaged Windows exe replaces itself."""
    return bool(getattr(sys, "frozen", False)) and os.name == "nt"


def current_exe() -> Path:
    return Path(sys.executable).resolve()


def check_for_update(timeout: float = 5.0) -> Release | None:
    """Return the latest release if it's newer than this version, else None.
    Any network problem just means "no update"."""
    req = urllib.request.Request(API_URL, headers={
        "Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception:
        return None
    return release_from_api(data)


def release_from_api(data: dict) -> Release | None:
    tag = str(data.get("tag_name", ""))
    if not tag or data.get("draft") or data.get("prerelease") or not is_newer(tag):
        return None
    for asset in data.get("assets", []):
        if asset.get("name") == ASSET_NAME:
            return Release(tag.lstrip("vV"), asset["browser_download_url"],
                           int(asset.get("size", 0)), str(data.get("body") or ""))
    return None


def cleanup_old_versions() -> None:
    """Remove the previous exe left behind by the last update."""
    if not can_self_update():
        return
    for old in current_exe().parent.glob("DronePitchSorter.old*.exe"):
        try:
            old.unlink()
        except OSError:
            pass  # still running or locked; try again next start


def download_and_install(release: Release, on_progress: Callable[[float], None] | None = None) -> None:
    """Download the new exe, verify it, and swap it in. Raises on any problem,
    in which case the current exe is left exactly as it was."""
    exe = current_exe()
    new = exe.with_name(exe.stem + ".new.exe")
    old = exe.with_name("DronePitchSorter.old.exe")

    req = urllib.request.Request(release.url, headers={"User-Agent": USER_AGENT})
    got = 0
    with urllib.request.urlopen(req, timeout=30) as resp, open(new, "wb") as out:
        total = int(resp.headers.get("Content-Length") or release.size or 0)
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            if on_progress and total:
                on_progress(got / total)

    try:
        if release.size and got != release.size:
            raise OSError(f"Download incomplete ({got} of {release.size} bytes)")
        with open(new, "rb") as f:
            if f.read(2) != b"MZ":
                raise OSError("Downloaded file isn't a Windows program")
        if old.exists():
            old.unlink()
        exe.rename(old)            # a running exe can be renamed on Windows
        try:
            new.rename(exe)
        except OSError:
            old.rename(exe)        # put the original back
            raise
    except BaseException:
        try:
            new.unlink()
        except OSError:
            pass
        raise


def relaunch() -> None:
    """Start the (new) exe as a fresh process. The caller then exits."""
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # don't reuse this process's unpacked files
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(current_exe())], env=env, close_fds=True, creationflags=flags,
                     cwd=str(current_exe().parent))
