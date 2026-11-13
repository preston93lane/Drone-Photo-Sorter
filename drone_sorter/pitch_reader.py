"""Read DJI gimbal pitch straight from a photo's embedded XMP text.

DJI drones write their flight/gimbal metadata as a plain-text XMP packet, e.g.

    drone-dji:GimbalPitchDegree="-40.00"

For JPEGs the packet sits in the first few KB of the file. DNGs also embed it
as text, but it can sit further in, so we keep reading in chunks until found.
No ExifTool or other external program is needed.
"""

from __future__ import annotations

import re
from pathlib import Path

# Matches both XMP serialisations:
#   attribute form:  drone-dji:GimbalPitchDegree="-40.00"
#   element form:    <drone-dji:GimbalPitchDegree>-40.00</drone-dji:GimbalPitchDegree>
_PITCH_RE = re.compile(
    rb"drone-dji:GimbalPitchDegree\s*(?:=\s*[\"']|>)\s*([+-]?\d+(?:\.\d+)?)"
)

FIRST_CHUNK = 256 * 1024     # covers the XMP packet of every DJI JPEG
CHUNK = 1024 * 1024          # follow-up chunk size for DNG / unusual files
_OVERLAP = 128               # so a tag split across two chunks is still found


def parse_pitch(data: bytes) -> float | None:
    """Return the gimbal pitch found in a byte buffer, or None."""
    m = _PITCH_RE.search(data)
    return float(m.group(1)) if m else None


def read_gimbal_pitch(path: str | Path, full_scan: bool = True) -> float | None:
    """Return the gimbal pitch (degrees) of a DJI photo, or None if absent.

    Reads only the first 256 KB in the common case. If the tag isn't there and
    ``full_scan`` is True, keeps scanning the rest of the file in 1 MB chunks.
    """
    with open(path, "rb") as f:
        buf = f.read(FIRST_CHUNK)
        pitch = parse_pitch(buf)
        if pitch is not None or not full_scan:
            return pitch
        tail = buf[-_OVERLAP:]
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                return None
            buf = tail + chunk
            pitch = parse_pitch(buf)
            if pitch is not None:
                return pitch
            tail = buf[-_OVERLAP:]
