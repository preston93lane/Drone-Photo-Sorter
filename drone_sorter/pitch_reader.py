"""Read DJI gimbal pitch straight from a photo's embedded XMP text.

DJI drones write their flight/gimbal metadata as a plain-text XMP packet, e.g.

    drone-dji:GimbalPitchDegree="-40.00"

For JPEGs the packet sits in the first few KB of the file. DNGs also embed it
as text, but it can sit further in, so we keep reading in chunks until found.
No ExifTool or other external program is needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Matches both XMP serialisations:
#   attribute form:  drone-dji:GimbalPitchDegree="-40.00"
#   element form:    <drone-dji:GimbalPitchDegree>-40.00</drone-dji:GimbalPitchDegree>
_PITCH_RE = re.compile(
    rb"drone-dji:GimbalPitchDegree\s*(?:=\s*[\"']|>)\s*([+-]?\d+(?:\.\d+)?)"
)

FIRST_CHUNK = 256 * 1024     # covers the XMP packet of every DJI JPEG
CHUNK = 1024 * 1024          # follow-up chunk size for DNG / unusual files
_OVERLAP = 4096             # so tags split across two chunks are still found


_NUM = rb"\s*(?:=\s*[\"']|>)\s*([+-]?\d+(?:\.\d+)?)"
_FIELDS = {
    "yaw": re.compile(rb"drone-dji:GimbalYawDegree" + _NUM),
    "lat": re.compile(rb"drone-dji:GpsLatitude" + _NUM),
    # some DJI firmware spells it "Longtitude"
    "lon": re.compile(rb"drone-dji:GpsLongt?itude" + _NUM),
}


@dataclass(frozen=True)
class PhotoMeta:
    pitch: float | None = None
    yaw: float | None = None     # compass direction the camera faces (0 = north)
    lat: float | None = None
    lon: float | None = None


def _parse_meta(data: bytes, pitch: float) -> PhotoMeta:
    vals = {}
    for key, rx in _FIELDS.items():
        m = rx.search(data)
        vals[key] = float(m.group(1)) if m else None
    return PhotoMeta(pitch, **vals)


def parse_pitch(data: bytes) -> float | None:
    """Return the gimbal pitch found in a byte buffer, or None."""
    m = _PITCH_RE.search(data)
    return float(m.group(1)) if m else None


def read_photo_meta(path: str | Path, full_scan: bool = True) -> PhotoMeta:
    """Gimbal pitch plus camera direction and GPS, from the photo's DJI XMP."""
    with open(path, "rb") as f:
        buf = f.read(FIRST_CHUNK)
        pitch = parse_pitch(buf)
        if pitch is not None:
            return _parse_meta(buf, pitch)
        if not full_scan:
            return PhotoMeta()
        tail = buf[-_OVERLAP:]
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                return PhotoMeta()
            buf = tail + chunk
            pitch = parse_pitch(buf)
            if pitch is not None:
                return _parse_meta(buf, pitch)
            tail = buf[-_OVERLAP:]


def read_gimbal_pitch(path: str | Path, full_scan: bool = True) -> float | None:
    """Return the gimbal pitch (degrees) of a DJI photo, or None if absent."""
    return read_photo_meta(path, full_scan).pitch
