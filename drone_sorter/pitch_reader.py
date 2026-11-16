"""Read DJI gimbal pitch straight from a photo's embedded XMP text.

DJI drones write their flight/gimbal metadata as a plain-text XMP packet, e.g.

    drone-dji:GimbalPitchDegree="-40.00"

For JPEGs the packet sits in the first few KB of the file. DNGs also embed it
as text, but it can sit further in, so we keep reading in chunks until found.
No ExifTool or other external program is needed.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from datetime import datetime
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
    taken: float | None = None   # capture time in seconds (only differences matter)


# --- capture time ----------------------------------------------------------

_XMP_DATE = re.compile(
    rb"xmp:CreateDate\s*(?:=\s*[\"']|>)\s*(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(\.\d+)?")
_EXIF_ASCII_DATE = re.compile(rb"(\d{4}):(\d\d):(\d\d) (\d\d):(\d\d):(\d\d)")
_EPOCH = datetime(1970, 1, 1)


def _seconds(y, mo, d, h, mi, se, frac: float = 0.0) -> float | None:
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(se))
    except ValueError:
        return None
    return (dt - _EPOCH).total_seconds() + frac


def _exif_time(head: bytes) -> float | None:
    """DateTimeOriginal + SubSecTimeOriginal from the EXIF block (JPEG or TIFF/DNG)."""
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        tiff = head
    else:
        i = head.find(b"Exif\x00\x00")
        if i < 0:
            return None
        tiff = head[i + 6:]
    try:
        bo = "<" if tiff[:2] == b"II" else ">"

        def ifd_entries(off):
            (n,) = struct.unpack_from(bo + "H", tiff, off)
            for k in range(n):
                yield struct.unpack_from(bo + "HHII", tiff, off + 2 + 12 * k) + (off + 2 + 12 * k + 8,)

        def ascii_val(count, value, value_pos):
            raw = tiff[value_pos:value_pos + count] if count <= 4 else tiff[value:value + count]
            return raw.split(b"\x00")[0].decode("ascii", "ignore").strip()

        (ifd0,) = struct.unpack_from(bo + "I", tiff, 4)
        exif_off = next((v for tag, _, _, v, _ in ifd_entries(ifd0) if tag == 0x8769), None)
        if exif_off is None:
            return None
        date = sub = None
        for tag, _, count, value, pos in ifd_entries(exif_off):
            if tag == 0x9003:
                date = ascii_val(count, value, pos)
            elif tag == 0x9291:
                sub = ascii_val(count, value, pos)
        m = _EXIF_ASCII_DATE.match(date.encode()) if date else None
        if not m:
            return None
        frac = float("0." + sub) if sub and sub.isdigit() else 0.0
        return _seconds(*m.groups(), frac)
    except (struct.error, ValueError, IndexError):
        return None


def parse_time(head: bytes, xmp: bytes) -> float | None:
    t = _exif_time(head)
    if t is not None:
        return t
    m = _XMP_DATE.search(xmp)
    if m:
        frac = float(m.group(7).decode()) if m.group(7) else 0.0
        return _seconds(*m.groups()[:6], frac)
    m = _EXIF_ASCII_DATE.search(head)
    return _seconds(*m.groups()) if m else None


def _parse_meta(data: bytes, pitch: float, head: bytes | None = None) -> PhotoMeta:
    vals = {}
    for key, rx in _FIELDS.items():
        m = rx.search(data)
        vals[key] = float(m.group(1)) if m else None
    return PhotoMeta(pitch, **vals, taken=parse_time(head if head is not None else data, data))


def parse_pitch(data: bytes) -> float | None:
    """Return the gimbal pitch found in a byte buffer, or None."""
    m = _PITCH_RE.search(data)
    return float(m.group(1)) if m else None


def read_photo_meta(path: str | Path, full_scan: bool = True) -> PhotoMeta:
    """Gimbal pitch plus camera direction and GPS, from the photo's DJI XMP."""
    with open(path, "rb") as f:
        buf = f.read(FIRST_CHUNK)
        head = buf
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
                return _parse_meta(buf, pitch, head)
            tail = buf[-_OVERLAP:]


def read_gimbal_pitch(path: str | Path, full_scan: bool = True) -> float | None:
    """Return the gimbal pitch (degrees) of a DJI photo, or None if absent."""
    return read_photo_meta(path, full_scan).pitch
